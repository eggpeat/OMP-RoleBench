"""Privacy-safe diagnostic task authoring and admission helpers."""

from __future__ import annotations

import ctypes
import errno
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import uuid
from typing import Final, Sequence

from .accounting import AccountingError, classify_attempt
from .contracts import (
    BUILTIN_ROLES,
    ContractError,
    JSONObject,
    JSONValue,
    canonical_sha256,
    file_sha256,
    task_execution_sha256,
    tree_sha256,
    validate_value,
)
from .image_identity import (
    ImageIdentityError,
    image_config_digest_from_archive,
)
from .worker import WorkerError, capture_command, local_docker_argv

_MAX_SESSION_BYTES: Final = 32 * 1024 * 1024
_MAX_LINE_BYTES: Final = 2 * 1024 * 1024
_MAX_TOML_BYTES: Final = 1024 * 1024
_MAX_FILES: Final = 2048
_MAX_FILE_BYTES: Final = 32 * 1024 * 1024
_MAX_TREE_BYTES: Final = 256 * 1024 * 1024
_MAX_JSON_NODES: Final = 100_000
_MAX_JSON_DEPTH: Final = 128
_MAX_SESSION_MESSAGES: Final = 1_000_000
_MAX_SIGNALS: Final = 100_000
_MAX_TREE_DEPTH: Final = 128
_MAX_PATH_BYTES: Final = 4096
_CHUNK: Final = 1024 * 1024
_CORRECTION = re.compile(
    r"(?:\bversioned\s+correction\b|\b(?:correct(?:ion|ed)?|fix(?:ed)?|revis(?:e|ed|ion))\b.{0,80}\b(?:v(?:ersion)?\s*\d+|revision\s*\d+)\b|\b(?:v(?:ersion)?\s*\d+|revision\s*\d+)\b.{0,80}\b(?:correct(?:ion|ed)?|fix(?:ed)?|revis(?:e|ed|ion))\b)",
    re.IGNORECASE | re.DOTALL,
)
_TEST_PASS = re.compile(r"\btests?\b.{0,80}\b(?:pass(?:ed|es)?|green|succeed(?:ed|s)?)\b", re.IGNORECASE | re.DOTALL)
_TEST_FAIL = re.compile(r"(?:\blate\s+test\s+failure\b|\btests?\b.{0,80}\b(?:fail(?:ed|s|ure)?|error(?:ed|s)?)\b|\b(?:fail(?:ed|s|ure)?|error(?:ed|s)?)\b.{0,80}\btests?\b)", re.IGNORECASE | re.DOTALL)


class TaskWorkflowError(Exception):
    """An unsafe, malformed, or unreadable workflow operation."""


class TaskAdmissionError(TaskWorkflowError):
    """A well-formed artifact that is not eligible for admission or execution."""


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _role(role_hint: str | None) -> None:
    if role_hint is not None and role_hint not in BUILTIN_ROLES:
        raise TaskWorkflowError("unknown role hint")


def _open_regular(path: Path) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise TaskWorkflowError("source is not a safe regular file")
        return fd
    except TaskWorkflowError:
        try:
            os.close(fd)
        except UnboundLocalError:
            pass
        raise
    except OSError as error:
        raise TaskWorkflowError("cannot open source safely") from error


def _read_fd(fd: int, limit: int) -> bytes:
    before = os.fstat(fd)
    if before.st_size > limit:
        raise TaskWorkflowError("source exceeds size limit")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(
            fd,
            min(_CHUNK, limit + 1 - total),
        )
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise TaskWorkflowError("source exceeds size limit")
        chunks.append(chunk)
    after = os.fstat(fd)
    if (
        total != before.st_size
        or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
    ):
        raise TaskWorkflowError(
            "source changed while being read"
        )
    return b"".join(chunks)


def _safe_bytes(path: Path, limit: int) -> bytes:
    fd = _open_regular(path)
    try:
        return _read_fd(fd, limit)
    finally:
        os.close(fd)


def _strings(value: object) -> list[str]:
    result: list[str] = []
    stack: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    keys = ("text", "content", "message", "output", "result")
    while stack:
        item, depth = stack.pop()
        visited += 1
        if visited > _MAX_JSON_NODES:
            raise TaskWorkflowError(
                "session message exceeds JSON node limit"
            )
        if depth > _MAX_JSON_DEPTH:
            raise TaskWorkflowError(
                "session message exceeds JSON depth limit"
            )
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, list):
            stack.extend(
                (child, depth + 1)
                for child in reversed(item)
            )
        elif isinstance(item, dict):
            selected = [
                item[key]
                for key in keys
                if key in item
            ]
            stack.extend(
                (child, depth + 1)
                for child in reversed(selected)
            )
    return result


def _v3_message(row: object, *, inherited_v3: bool = False) -> bool:
    if not isinstance(row, dict) or row.get("type") != "message":
        return False
    version = row.get("version", row.get("schema_version"))
    return inherited_v3 or version == 3 or version in {
        "3", "v3", "omp.session/v3", "omp.session-message/v3"
    }


def scan_session_candidates(path: Path, *, role_hint: str | None = None) -> JSONObject:
    """Scan exactly one named OMP JSONL v3 file and return non-linkable signals."""

    _role(role_hint)
    data = _safe_bytes(Path(path).expanduser(), _MAX_SESSION_BYTES)
    session_v3 = False
    messages: list[str] = []
    for line_number, line in enumerate(data.splitlines(), 1):
        if len(line) > _MAX_LINE_BYTES:
            raise TaskWorkflowError("session row exceeds size limit")
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            RecursionError,
        ) as error:
            raise TaskWorkflowError(
                f"invalid JSONL at row {line_number}"
            ) from error
        version = row.get("version", row.get("schema_version")) if isinstance(row, dict) else None
        session_v3 = session_v3 or version == 3 or version in {
            "3", "v3", "omp.session/v3", "omp.session-message/v3"
        }
        if _v3_message(row, inherited_v3=session_v3):
            messages.append("\n".join(_strings(row.get("message", row.get("content", "")))))
            if len(messages) > _MAX_SESSION_MESSAGES:
                raise TaskWorkflowError(
                    "session exceeds message limit"
                )
    signals: list[JSONValue] = []
    tests_passed = False
    for ordinal, text in enumerate(messages, 1):
        if _CORRECTION.search(text):
            signals.append({"ordinal": ordinal, "kind": "versioned-correction"})
        if _TEST_FAIL.search(text) and (tests_passed or "late test failure" in text.lower()):
            signals.append({"ordinal": ordinal, "kind": "late-test-failure"})
        if len(signals) > _MAX_SIGNALS:
            raise TaskWorkflowError(
                "session exceeds signal limit"
            )
        if _TEST_PASS.search(text):
            tests_passed = True
    signals.sort(
        key=lambda item: (
            int(item["ordinal"]),
            str(item["kind"]),
        )
    )
    candidate: JSONObject = {
        "schema_version": "omp.task-candidate/v1",
        "origin": {
            "kind": "omp-session",
            "source_version": "v3",
            "candidate_ref": str(uuid.uuid4()),
        },
        "capability_hints": [],
        "review": {"privacy": "required", "license": "required", "publication": "prohibited"},
        "entry_count": len(messages),
        "signals": signals,
    }
    if role_hint is not None:
        candidate["role_hint"] = role_hint
    return candidate


def _destination(root: Path, path: Path, *, candidate: bool) -> Path:
    value = path.expanduser()
    if not value.is_absolute():
        value = root / value
    absolute = Path(os.path.abspath(value))
    if absolute == root or not _within(absolute, root):
        raise TaskWorkflowError("destination must be inside repository")
    if candidate and not _within(absolute, root / ".rolebench" / "candidates"):
        raise TaskWorkflowError("candidate destination must be private")
    current = root
    for part in absolute.relative_to(root).parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TaskWorkflowError("destination has an unsafe parent")
    return absolute


def _parents(root: Path, parent: Path) -> None:
    current = root
    for part in parent.relative_to(root).parts:
        current /= part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise TaskWorkflowError("output parent is unsafe")
        else:
            os.chmod(current, 0o700, follow_symlinks=False)


def _open_source_directory(source: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as error:
        raise TaskWorkflowError("cannot open import source safely") from error
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise TaskWorkflowError("import source is not a directory")
    return fd


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise TaskWorkflowError("short write")
        view = view[count:]


def _capture_tree(source_fd: int, destination: Path) -> None:
    state = [0, 0]
    root_before = os.fstat(source_fd)

    def capture(
        directory_fd: int,
        relative: Path,
        target: Path,
        depth: int,
    ) -> None:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as error:
            raise TaskWorkflowError("cannot enumerate import source") from error
        for name in names:
            if not name or name in {".", ".."} or "/" in name or "\x00" in name:
                raise TaskWorkflowError("invalid import entry name")
            state[0] += 1
            if (
                state[0] > _MAX_FILES
                or depth + 1 > _MAX_TREE_DEPTH
            ):
                raise TaskWorkflowError(
                    "import source exceeds structural limits"
                )
            child_relative = relative / name
            try:
                encoded_name = child_relative.as_posix().encode(
                    "utf-8"
                )
            except UnicodeEncodeError as error:
                raise TaskWorkflowError(
                    "import entry name is not UTF-8"
                ) from error
            if len(encoded_name) > _MAX_PATH_BYTES:
                raise TaskWorkflowError(
                    "import entry path exceeds size limit"
                )
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as error:
                raise TaskWorkflowError("cannot inspect import entry") from error
            if stat.S_ISDIR(info.st_mode):
                child_target = target / name
                child_target.mkdir(mode=0o700)
                flags = (
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_CLOEXEC
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    child_fd = os.open(
                        name,
                        flags,
                        dir_fd=directory_fd,
                    )
                except OSError as error:
                    raise TaskWorkflowError(
                        "cannot open import directory safely"
                    ) from error
                try:
                    opened_child = os.fstat(child_fd)
                    if (
                        opened_child.st_dev,
                        opened_child.st_ino,
                        opened_child.st_mtime_ns,
                        opened_child.st_ctime_ns,
                    ) != (
                        info.st_dev,
                        info.st_ino,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                    ):
                        raise TaskWorkflowError(
                            "import directory changed while opening"
                        )
                    capture(
                        child_fd,
                        child_relative,
                        child_target,
                        depth + 1,
                    )
                    closed_child = os.fstat(child_fd)
                    if (
                        closed_child.st_dev,
                        closed_child.st_ino,
                        closed_child.st_mtime_ns,
                        closed_child.st_ctime_ns,
                    ) != (
                        opened_child.st_dev,
                        opened_child.st_ino,
                        opened_child.st_mtime_ns,
                        opened_child.st_ctime_ns,
                    ):
                        raise TaskWorkflowError(
                            "import directory changed during capture"
                        )
                finally:
                    os.close(child_fd)
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise TaskWorkflowError("import source contains a prohibited file type")
            state[1] += info.st_size
            if (
                info.st_size > _MAX_FILE_BYTES
                or state[1] > _MAX_TREE_BYTES
            ):
                raise TaskWorkflowError(
                    "import source exceeds capture limits"
                )
            flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            try:
                input_fd = os.open(name, flags, dir_fd=directory_fd)
            except OSError as error:
                raise TaskWorkflowError("cannot open import file safely") from error
            output_fd = -1
            try:
                opened = os.fstat(input_fd)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mtime_ns,
                        opened.st_ctime_ns,
                    )
                    != (
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                    )
                ):
                    raise TaskWorkflowError(
                        "import source changed during capture"
                    )
                executable = bool(opened.st_mode & 0o111)
                mode = 0o700 if executable else 0o600
                output_fd = os.open(
                    target / name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_CLOEXEC,
                    mode,
                )
                copied = 0
                while True:
                    chunk = os.read(input_fd, _CHUNK)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > _MAX_FILE_BYTES:
                        raise TaskWorkflowError("import file changed beyond size limit")
                    if output_fd >= 0:
                        _write_all(output_fd, chunk)
                final = os.fstat(input_fd)
                if (
                    copied != opened.st_size
                    or (
                        final.st_dev,
                        final.st_ino,
                        final.st_size,
                        final.st_mtime_ns,
                        final.st_ctime_ns,
                    )
                    != (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mtime_ns,
                        opened.st_ctime_ns,
                    )
                ):
                    raise TaskWorkflowError(
                        "import source changed during capture"
                    )
                if output_fd >= 0:
                    os.fchmod(output_fd, 0o700 if executable else 0o600)
                    os.fsync(output_fd)
            finally:
                os.close(input_fd)
                if output_fd >= 0:
                    os.close(output_fd)

    capture(source_fd, Path(), destination, 0)
    root_after = os.fstat(source_fd)
    if (
        root_after.st_dev,
        root_after.st_ino,
        root_after.st_mtime_ns,
        root_after.st_ctime_ns,
    ) != (
        root_before.st_dev,
        root_before.st_ino,
        root_before.st_mtime_ns,
        root_before.st_ctime_ns,
    ):
        raise TaskWorkflowError(
            "import root changed during capture"
        )


def _toml_text(value: object, name: str, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise TaskWorkflowError(f"omp-gym {name} must be a nonempty string")
    return value


def _rename_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise TaskWorkflowError("atomic no-overwrite rename is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result == 0:
        return
    number = ctypes.get_errno()
    if number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise TaskWorkflowError("destination already exists")
    raise TaskWorkflowError("cannot atomically publish candidate")


def import_omp_gym_task(
    root: Path,
    source: Path,
    destination: Path,
    *,
    source_version: str,
    license_expression: str,
    role_hint: str | None = None,
    capability_hints: Sequence[str] = (),
) -> JSONObject:
    """Independently parse and privately capture one omp-gym task."""

    _role(role_hint)
    if not source_version or not license_expression:
        raise TaskWorkflowError("source version and license expression are required")
    if not all(isinstance(tag, str) and tag for tag in capability_hints):
        raise TaskWorkflowError("invalid capability hint")
    resolved_root = Path(root).expanduser().resolve(strict=True)
    source_path = Path(source).expanduser()
    output = _destination(resolved_root, Path(destination), candidate=True)
    source_absolute = Path(os.path.abspath(source_path))
    if _within(output, source_absolute):
        raise TaskWorkflowError("destination must not be inside source")
    if output.exists() or output.is_symlink():
        raise TaskWorkflowError("destination already exists")
    source_fd = _open_source_directory(source_path)
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            toml_fd = os.open("task.toml", flags, dir_fd=source_fd)
        except OSError as error:
            raise TaskWorkflowError("cannot open task metadata safely") from error
        try:
            info = os.fstat(toml_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise TaskWorkflowError("task metadata is not a safe regular file")
            raw_toml = _read_fd(toml_fd, _MAX_TOML_BYTES)
        finally:
            os.close(toml_fd)
        try:
            metadata = tomllib.loads(raw_toml.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise TaskWorkflowError("invalid task metadata") from error
        prompt = _toml_text(metadata.get("prompt"), "prompt")
        test_command = metadata.get("test_command")
        if (
            not isinstance(test_command, list)
            or not test_command
            or not all(
                isinstance(item, str) and item
                for item in test_command
            )
        ):
            raise TaskWorkflowError(
                "omp-gym test_command must be nonempty strings"
            )
        tools = metadata.get(
            "tools",
            "read,bash,edit,write,grep,glob",
        )
        if not isinstance(tools, str) or not tools:
            raise TaskWorkflowError("omp-gym tools must be a string")
        raw_max_time = metadata.get("max_time", "300")
        if (
            not isinstance(raw_max_time, str)
            or not raw_max_time.isdecimal()
        ):
            raise TaskWorkflowError(
                "omp-gym max_time must be integer seconds"
            )
        max_time = int(raw_max_time)
        if not 0 < max_time <= 86400:
            raise TaskWorkflowError(
                "omp-gym max_time is outside the accepted range"
            )
        fidelity = metadata.get("fidelity")
        if isinstance(fidelity, (dict, list)):
            raise TaskWorkflowError("omp-gym fidelity must be a scalar")

        _parents(resolved_root, output.parent)
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
        os.chmod(temporary, 0o700)
        try:
            workspace = temporary / "workspace"
            workspace.mkdir(mode=0o700)
            workspace_flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                workspace_fd = os.open(
                    "workspace",
                    workspace_flags,
                    dir_fd=source_fd,
                )
            except OSError as error:
                raise TaskWorkflowError(
                    "cannot open omp-gym workspace safely"
                ) from error
            try:
                _capture_tree(
                    workspace_fd,
                    workspace,
                )
            finally:
                os.close(workspace_fd)
            workspace_digest = tree_sha256(workspace)
            source_digest = sha256(
                len(raw_toml).to_bytes(8, "big")
                + raw_toml
                + bytes.fromhex(workspace_digest)
            ).hexdigest()
            prompt_path = temporary / "prompt.txt"
            prompt_fd = os.open(prompt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            try:
                _write_all(prompt_fd, prompt.encode("utf-8"))
                os.fchmod(prompt_fd, 0o600)
                os.fsync(prompt_fd)
            finally:
                os.close(prompt_fd)
            legacy: JSONObject = {
                "test_command": list(test_command),
                "tools": tools,
                "max_time_seconds": max_time,
                "fidelity": fidelity,
            }
            candidate_value: JSONObject = {
                "schema_version": "omp.task-candidate/v1",
                "origin": {"kind": "omp-gym", "source_version": source_version, "digest_sha256": source_digest},
                "capability_hints": list(capability_hints),
                "review": {"privacy": "required", "license": "required", "license_expression": license_expression, "publication": "prohibited"},
                "entry_count": 0,
                "signals": [],
                "assets": {
                    "prompt": {"path": "prompt.txt", "kind": "file", "digest_sha256": file_sha256(prompt_path)},
                    "workspace": {"path": "workspace", "kind": "tree", "digest_sha256": workspace_digest},
                },
                "legacy": legacy,
            }
            if role_hint is not None:
                candidate_value["role_hint"] = role_hint
            staging_candidate = (
                temporary.relative_to(resolved_root)
                / "candidate.json"
            )
            checked = validate_value(
                resolved_root,
                "task-candidate",
                candidate_value,
                staging_candidate,
            )
            if not checked.valid:
                raise TaskWorkflowError("generated candidate failed contract validation")
            candidate_fd = os.open(temporary / "candidate.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            try:
                encoded = (json.dumps(candidate_value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
                _write_all(candidate_fd, encoded)
                os.fchmod(candidate_fd, 0o600)
                os.fsync(candidate_fd)
            finally:
                os.close(candidate_fd)
            directory_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            _rename_noreplace(temporary, output)
            parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            return candidate_value
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
    finally:
        os.close(source_fd)


def _object_with_digest(path: Path) -> tuple[JSONObject, str]:
    data = _safe_bytes(path, _MAX_FILE_BYTES)
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise TaskWorkflowError("artifact is not valid JSON") from error
    if not isinstance(value, dict):
        raise TaskWorkflowError("artifact is not an object")
    return value, sha256(data).hexdigest()


def _object(path: Path) -> JSONObject:
    return _object_with_digest(path)[0]


def _artifact(root: Path, path: Path) -> Path:
    value = path if path.is_absolute() else root / path
    absolute = Path(os.path.abspath(value))
    if not _within(absolute, root):
        raise TaskWorkflowError("artifact escapes repository")
    current = root
    for part in absolute.relative_to(root).parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError as error:
            raise TaskWorkflowError("artifact parent is unavailable") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TaskWorkflowError("artifact parent is unsafe")
    return absolute


def _diagnostics(result: object) -> list[str]:
    return [f"{item.file}:{item.json_path}: {item.message}" for item in getattr(result, "diagnostics", ())]


def _expected_decision(task: JSONObject, qualification: JSONObject) -> str:
    checks = qualification.get("checks")
    if not isinstance(checks, dict):
        return "rejected"
    is_v2 = (
        qualification.get("schema_version")
        == "omp.task-qualification/v2"
    )
    if is_v2:
        objective = task.get("objective")
        observation = (
            objective.get("observation")
            if isinstance(objective, dict)
            else None
        )
        artifact_kind = (
            observation.get("artifact_kind")
            if isinstance(observation, dict)
            else None
        )
        authority = (
            observation.get("authority")
            if isinstance(observation, dict)
            else None
        )
        expected_observation_authority = (
            "pass"
            if (
                artifact_kind == "data-only"
                and authority == "host-process"
            )
            else "fail"
        )
        if (
            checks.get("observation_authority")
            != expected_observation_authority
            or expected_observation_authority != "pass"
        ):
            return "rejected"
        simple_mandatory = (
            "privacy",
            "license",
            "runner_isolation",
            "verifier_isolation",
            "observation_authority",
            "determinism",
            "infrastructure_classification",
        )
    else:
        simple_mandatory = (
            "privacy",
            "license",
            "verifier_isolation",
            "determinism",
            "infrastructure_classification",
        )
    if any(checks.get(name) != "pass" for name in simple_mandatory):
        return "rejected"
    for name in (
        "baseline_fails",
        "reference_passes",
        "tamper_resistance",
    ):
        evidence_check = checks.get(name)
        if (
            not isinstance(evidence_check, dict)
            or evidence_check.get("result") != "pass"
        ):
            return "rejected"
    discrimination = checks.get("discrimination")
    if discrimination == "calibration-required":
        return "calibration-required"
    return "rejected"


def _check_task_qualification_values(
    root: Path,
    task_file: Path,
    qualification_file: Path,
    task: JSONObject,
    qualification: JSONObject,
) -> JSONObject:
    task_relative = task_file.relative_to(root)
    qualification_relative = qualification_file.relative_to(root)
    diagnostics = _diagnostics(
        validate_value(
            root,
            "diagnostic-task",
            task,
            task_relative,
        )
    )
    diagnostics.extend(
        _diagnostics(
            validate_value(
                root,
                "task-qualification",
                qualification,
                qualification_relative,
            )
        )
    )
    for field in ("task_id", "task_version", "content_digest_sha256"):
        if qualification.get(field) != task.get(field):
            diagnostics.append(
                f"qualification {field} does not match task"
            )
    source_task = qualification.get("source_task")
    if not isinstance(source_task, dict):
        diagnostics.append("qualification source_task is missing")
    else:
        if source_task.get("path") != task_relative.as_posix():
            diagnostics.append(
                "qualification source_task path does not match task"
            )
        if source_task.get("digest_sha256") != canonical_sha256(task):
            diagnostics.append(
                "qualification source_task digest does not match task"
            )
    if qualification.get("reviews") != task.get("reviews"):
        diagnostics.append(
            "qualification reviews do not exactly match task reviews"
        )
    checks = qualification.get("checks")
    commands: list[str] = []
    recaptured_reports: list[JSONObject] = []
    recaptured_digests: list[str] = []
    admission_agents = task.get("admission_agents")
    probe_outcomes = {
        "baseline_fails": ("baseline", "rejected"),
        "reference_passes": ("reference", "accepted"),
        "tamper_resistance": ("tamper", "rejected"),
    }
    if isinstance(checks, dict):
        for name, (
            probe,
            verifier_outcome,
        ) in probe_outcomes.items():
            evidence_check = checks.get(name)
            if not isinstance(evidence_check, dict):
                diagnostics.append(
                    f"qualification {name} evidence is missing"
                )
                continue
            evidence = evidence_check.get("evidence")
            if not isinstance(evidence, list) or len(evidence) < 2:
                diagnostics.append(
                    f"qualification {name} repeated evidence is missing"
                )
                continue
            paths: list[Path] = []
            malformed = False
            for index, reference in enumerate(evidence):
                if (
                    not isinstance(reference, dict)
                    or not isinstance(reference.get("path"), str)
                ):
                    diagnostics.append(
                        f"qualification {name} evidence {index} is invalid"
                    )
                    malformed = True
                else:
                    paths.append(Path(reference["path"]))
            if malformed:
                continue
            try:
                probe_reports, command_digest, recaptured = (
                    _qualification_group(
                        root,
                        tuple(paths),
                        task,
                        probe=probe,
                        verifier_outcome=verifier_outcome,
                    )
                )
            except (OSError, TaskWorkflowError) as error:
                diagnostics.append(
                    f"qualification {name} evidence is invalid: {error}"
                )
                continue
            commands.append(command_digest)
            recaptured_reports.extend(probe_reports)
            recaptured_digests.extend(
                str(reference["digest_sha256"])
                for reference in recaptured
            )
            if evidence_check.get(
                "command_digest_sha256"
            ) != command_digest:
                diagnostics.append(
                    f"qualification {name} command digest does not match reports"
                )
            if evidence != recaptured:
                diagnostics.append(
                    f"qualification {name} evidence digests do not match reports"
                )
            expected_agent = (
                admission_agents.get(probe)
                if isinstance(admission_agents, dict)
                else None
            )
            expected_command = (
                expected_agent.get("config_digest_sha256")
                if isinstance(expected_agent, dict)
                else None
            )
            if command_digest != expected_command:
                diagnostics.append(
                    f"qualification {name} reports do not use the task {probe} agent"
                )
        if len(commands) == 3:
            if len(set(recaptured_digests)) != len(
                recaptured_digests
            ):
                diagnostics.append(
                    "qualification reports must be content-distinct"
                )
            run_ids = [
                report.get("run_id")
                for report in recaptured_reports
            ]
            if (
                None in run_ids
                or len(set(run_ids)) != len(run_ids)
            ):
                diagnostics.append(
                    "qualification report run ids must be distinct"
                )
        if len(commands) == 3 and len(set(commands)) != 3:
            diagnostics.append(
                "qualification probe command digests must differ"
            )
    is_v2 = (
        qualification.get("schema_version")
        == "omp.task-qualification/v2"
    )
    provenance_name = (
        "evaluation_provenance" if is_v2 else "verifier_provenance"
    )
    provenance = qualification.get(provenance_name)
    runner = task.get("runner")
    verifier = task.get("verifier")
    reviews = task.get("reviews")
    verifier_review = (
        reviews.get("verifier")
        if isinstance(reviews, dict)
        else None
    )
    provenance_complete = (
        isinstance(provenance, dict)
        and isinstance(verifier, dict)
        and isinstance(verifier_review, dict)
        and (not is_v2 or isinstance(runner, dict))
    )
    if provenance_complete:
        if is_v2:
            if provenance.get("runner_image") != runner.get("image"):
                diagnostics.append(
                    "qualification runner image does not match task"
                )
            if provenance.get(
                "runner_config_digest_sha256"
            ) != runner.get("config_digest_sha256"):
                diagnostics.append(
                    "qualification runner config provenance does not match task"
                )
            if provenance.get("runner_platform") != runner.get(
                "platform"
            ):
                diagnostics.append(
                    "qualification runner platform provenance does not match task"
                )
        if provenance.get("verifier_image") != verifier.get("image"):
            diagnostics.append(
                "qualification verifier image does not match task"
            )
        if provenance.get(
            "verifier_config_digest_sha256"
        ) != verifier.get("config_digest_sha256"):
            diagnostics.append(
                "qualification verifier config provenance does not match task"
            )
        if provenance.get("verifier_platform") != verifier.get(
            "platform"
        ):
            diagnostics.append(
                "qualification verifier platform provenance does not match task"
            )
        if provenance.get("reviewer_id") != verifier_review.get(
            "reviewer"
        ):
            diagnostics.append(
                "qualification verifier reviewer does not match task review"
            )
        if provenance.get(
            "review_digest_sha256"
        ) != verifier_review.get("evidence_digest_sha256"):
            diagnostics.append(
                "qualification verifier review digest does not match task review"
            )
    else:
        diagnostics.append(
            "qualification evaluation provenance is missing"
            if is_v2
            else "qualification verifier provenance is missing"
        )
    observed = qualification.get("observed_mapping")
    assets = task.get("assets")
    agent = task.get("agent")
    mapping_complete = (
        isinstance(observed, dict)
        and isinstance(assets, dict)
        and isinstance(agent, dict)
        and isinstance(verifier, dict)
        and (not is_v2 or isinstance(runner, dict))
    )
    if mapping_complete:
        public = assets.get("public")
        private = assets.get("verifier_private")
        expected_mapping: JSONObject = {
            "task_digest_sha256": (
                task_execution_sha256(task)
                if is_v2
                else canonical_sha256(task)
            ),
            "public_tree_digest_sha256": (
                public.get("digest_sha256")
                if isinstance(public, dict)
                else None
            ),
            "verifier_private_tree_digest_sha256": (
                private.get("digest_sha256")
                if isinstance(private, dict)
                else None
            ),
            "agent_config_digest_sha256": agent.get(
                "config_digest_sha256"
            ),
            "verifier_config_digest_sha256": verifier.get(
                "config_digest_sha256"
            ),
        }
        if is_v2:
            expected_mapping["runner_config_digest_sha256"] = (
                runner.get("config_digest_sha256")
            )
        if observed != expected_mapping:
            diagnostics.append(
                "qualification observed mapping does not match task"
            )
    else:
        diagnostics.append("qualification observed mapping is missing")
    expected = _expected_decision(task, qualification)
    if qualification.get("decision") != expected:
        diagnostics.append(
            f"qualification decision must be {expected}"
        )
    decision = (
        qualification.get("decision")
        if isinstance(qualification.get("decision"), str)
        else expected
    )
    return {
        "valid": not diagnostics,
        "decision": decision,
        "diagnostics": sorted(set(diagnostics)),
    }


def check_task_qualification(
    root: Path,
    task_path: Path,
    qualification_path: Path,
) -> JSONObject:
    """Validate exact task/qualification cross-links and deterministic decision."""

    resolved_root = Path(root).expanduser().resolve(strict=True)
    task_file = _artifact(resolved_root, Path(task_path))
    qualification_file = _artifact(
        resolved_root,
        Path(qualification_path),
    )
    task = _object(task_file)
    qualification = _object(qualification_file)
    return _check_task_qualification_values(
        resolved_root,
        task_file,
        qualification_file,
        task,
        qualification,
    )


def _atomic_json(root: Path, output_path: Path, value: JSONObject) -> None:
    output = _destination(root, output_path, candidate=False)
    if output.exists() or output.is_symlink():
        raise TaskWorkflowError("output already exists")
    _parents(root, output.parent)
    encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(output, flags, 0o600)
    except OSError as error:
        raise TaskWorkflowError("cannot create output") from error
    try:
        _write_all(fd, encoded)
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        output.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)
    parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _docker_result(
    docker: str,
    arguments: Sequence[str],
    *,
    timeout: float = 60,
    output_limit: int = _MAX_SESSION_BYTES,
):
    try:
        argv = local_docker_argv(docker, *arguments)
        return capture_command(
            argv,
            timeout=timeout,
            output_limit=output_limit,
        )
    except (
        OSError,
        subprocess.SubprocessError,
        WorkerError,
    ) as error:
        raise TaskWorkflowError("container inspection failed") from error


def _docker_capture(
    docker: str,
    arguments: Sequence[str],
    *,
    output_limit: int = _MAX_SESSION_BYTES,
) -> bytes:
    result = _docker_result(
        docker,
        arguments,
        output_limit=output_limit,
    )
    if result.timed_out or result.overflowed or result.returncode != 0:
        raise TaskWorkflowError("container inspection failed")
    return result.stdout


def _docker_json(docker: str, arguments: Sequence[str]) -> JSONValue:
    try:
        return json.loads(_docker_capture(docker, arguments))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise TaskWorkflowError(
            "container image inspection returned invalid data"
        ) from error


def _image_digest_suffix(image: object) -> str:
    if not isinstance(image, str) or "@sha256:" not in image:
        raise TaskAdmissionError("task image reference is not digest-pinned")
    digest = image.rpartition("@sha256:")[2]
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise TaskAdmissionError("task image digest is malformed")
    return digest


def _effective_image_config_digest(
    docker: str,
    image: str,
    image_data: JSONObject,
    manifest_digest_sha256: str,
) -> str:
    engine_id = image_data.get("Id")
    if isinstance(engine_id, str):
        engine_digest = engine_id.removeprefix("sha256:")
        if (
            re.fullmatch(r"[0-9a-f]{64}", engine_digest) is not None
            and engine_digest != manifest_digest_sha256
        ):
            return engine_digest

    with tempfile.TemporaryDirectory(
        prefix="rolebench-image-identity-"
    ) as temporary:
        archive_path = Path(temporary) / "image.tar"
        result = _docker_result(
            docker,
            (
                "image",
                "save",
                "--output",
                str(archive_path),
                image,
            ),
            timeout=300,
            output_limit=1024 * 1024,
        )
        if (
            result.timed_out
            or result.overflowed
            or result.returncode != 0
        ):
            raise TaskAdmissionError(
                "container config identity export failed"
            )
        try:
            return image_config_digest_from_archive(
                archive_path,
                manifest_digest_sha256,
            )
        except ImageIdentityError as error:
            raise TaskAdmissionError(
                "container config identity is invalid"
            ) from error


def _inspect_image(
    docker: str,
    container: JSONObject,
    role: object,
    public_digest: str,
    private_digest: str,
    *,
    stage: str,
    include_stage_label: bool,
) -> JSONObject:
    if stage not in {"agent", "runner", "verifier"}:
        raise TaskAdmissionError("unknown task container stage")
    verifier = stage == "verifier"
    image = container.get("image")
    if not isinstance(image, str) or "@sha256:" not in image:
        raise TaskAdmissionError("container image is not digest-pinned")
    manifest_digest_sha256 = image.rpartition("@sha256:")[2]
    reference_digest = f"sha256:{manifest_digest_sha256}"
    image_data = _docker_json(
        docker,
        ("image", "inspect", image, "--format", "{{json .}}"),
    )
    if not isinstance(image_data, dict):
        raise TaskAdmissionError("container image inspection is ambiguous")
    descriptor = image_data.get("Descriptor")
    allowed_media_types = {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
    repo_digests = image_data.get("RepoDigests")
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("mediaType") not in allowed_media_types
        or descriptor.get("digest") != reference_digest
        or not isinstance(repo_digests, list)
        or image not in repo_digests
    ):
        raise TaskAdmissionError(
            "container image descriptor does not match pinned reference"
        )
    inspected_config = _effective_image_config_digest(
        docker,
        image,
        image_data,
        manifest_digest_sha256,
    )
    if inspected_config != container.get("config_digest_sha256"):
        raise TaskAdmissionError("container config digest does not match task")
    platform = container.get("platform")
    if (
        not isinstance(platform, dict)
        or image_data.get("Os") != platform.get("os")
        or image_data.get("Architecture") != platform.get("architecture")
        or image_data.get("Variant") != platform.get("variant")
    ):
        raise TaskAdmissionError("container platform does not match task")
    config = image_data.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise TaskAdmissionError("container image labels are missing")
    content_key = (
        "org.omp.rolebench.task.verifier-private-tree-sha256"
        if verifier
        else "org.omp.rolebench.task.public-tree-sha256"
    )
    required = {
        "org.omp.rolebench.task.role": role,
        content_key: private_digest if verifier else public_digest,
    }
    if include_stage_label:
        required["org.omp.rolebench.task.stage"] = stage
    observed = {
        str(key): str(value)
        for key, value in labels.items()
        if str(key).startswith("org.omp.rolebench.task.")
    }
    if observed != required:
        raise TaskAdmissionError("container image labels do not match task")
    return {
        "image": image,
        "config_digest_sha256": container["config_digest_sha256"],
        "platform": platform,
        "argv": container["argv"],
    }


def _safe_tar_tree(data: bytes, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    total = 0
    entries = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as error:
        raise TaskWorkflowError("container content archive is invalid") from error
    with archive:
        for member in archive:
            entries += 1
            if entries > _MAX_FILES:
                raise TaskWorkflowError(
                    "container archive exceeds entry limit"
                )
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise TaskWorkflowError("container archive escapes its root")
            parts = tuple(part for part in relative.parts if part not in {"", "."})
            if not parts:
                continue
            try:
                encoded_path = "/".join(parts).encode("utf-8")
            except UnicodeEncodeError as error:
                raise TaskWorkflowError(
                    "container archive path is not UTF-8"
                ) from error
            if (
                len(parts) > _MAX_TREE_DEPTH
                or len(encoded_path) > _MAX_PATH_BYTES
            ):
                raise TaskWorkflowError(
                    "container archive path exceeds limits"
                )
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise TaskWorkflowError("container archive has a prohibited file type")
            total += member.size
            if (
                member.size > _MAX_FILE_BYTES
                or total > _MAX_TREE_BYTES
            ):
                raise TaskWorkflowError(
                    "container archive exceeds size limits"
                )
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise TaskWorkflowError("container archive is incomplete")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            try:
                remaining = member.size
                while remaining:
                    chunk = source.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise TaskWorkflowError("container archive is truncated")
                    _write_all(fd, chunk)
                    remaining -= len(chunk)
                os.fchmod(fd, 0o700 if member.mode & 0o111 else 0o600)
                os.fsync(fd)
            finally:
                os.close(fd)


def _container_tree_digest(
    docker: str,
    image: str,
    container_path: str,
    *,
    must_exist: bool,
) -> str | None:
    capture_name = f"rolebench-capture-{secrets.token_hex(16)}"
    created = _docker_result(
        docker,
        (
            "create",
            "--name",
            capture_name,
            "--label",
            f"org.omp.rolebench.capture={capture_name}",
            "--runtime",
            "runsc",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--network",
            "none",
            "--entrypoint",
            "/bin/true",
            "--",
            image,
        ),
        output_limit=1024,
    )
    container_id = created.stdout.decode("ascii", "ignore").strip()
    target = (
        container_id
        if re.fullmatch(r"[0-9a-f]{12,64}", container_id) is not None
        else capture_name
    )
    try:
        if (
            created.returncode != 0
            or created.timed_out
            or created.overflowed
            or target == capture_name
        ):
            raise TaskWorkflowError("container content capture failed")
        inspected = _docker_json(docker, ("inspect", container_id))
        if (
            not isinstance(inspected, list)
            or len(inspected) != 1
            or not isinstance(inspected[0], dict)
        ):
            raise TaskWorkflowError("capture container inspection is invalid")
        inspect = inspected[0]
        config = inspect.get("Config")
        host = inspect.get("HostConfig")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if (
            inspect.get("Id") != container_id
            or inspect.get("Mounts") != []
            or not isinstance(config, dict)
            or config.get("Image") != image
            or not isinstance(labels, dict)
            or labels.get("org.omp.rolebench.capture") != capture_name
            or not isinstance(host, dict)
            or host.get("NetworkMode") != "none"
            or host.get("ReadonlyRootfs") is not True
            or host.get("Binds") not in (None, [])
            or host.get("Devices") not in (None, [])
        ):
            raise TaskAdmissionError(
                "capture container violates the inspection boundary"
            )
        copied = _docker_result(
            docker,
            (
                "cp",
                f"{container_id}:{container_path}",
                "-",
            ),
            output_limit=_MAX_TREE_BYTES + _MAX_FILES * 1024,
        )
        if copied.timed_out or copied.overflowed:
            raise TaskWorkflowError("container content capture exceeds limits")
        if copied.returncode != 0:
            missing = copied.stderr.lower()
            absent = (
                b"could not find" in missing
                or b"no such file" in missing
                or b"not found" in missing
            )
            if must_exist or not absent:
                raise TaskAdmissionError(
                    "required container content root is absent"
                )
            return None
        if not must_exist:
            raise TaskAdmissionError(
                "agent image contains verifier-private content"
            )
        temporary_parent = Path(
            tempfile.mkdtemp(prefix="rolebench-image-tree-")
        )
        temporary = temporary_parent / "capture"
        try:
            _safe_tar_tree(copied.stdout, temporary)
            children = list(temporary.iterdir())
            tree_root = (
                children[0]
                if len(children) == 1 and children[0].is_dir()
                else temporary
            )
            return tree_sha256(tree_root)
        except OSError as error:
            raise TaskWorkflowError(
                "cannot extract container content safely"
            ) from error
        finally:
            shutil.rmtree(temporary_parent)
    finally:
        cleanup = _docker_result(
            docker,
            (
                "rm",
                "--force",
                "--volumes",
                target,
            ),
            timeout=30,
            output_limit=1024,
        )
        missing = b"no such container" in cleanup.stderr.lower()
        if (
            cleanup.timed_out
            or cleanup.overflowed
            or (cleanup.returncode != 0 and not missing)
        ):
            raise TaskWorkflowError(
                "capture container cleanup failed"
            )


def _all_true_mapping(value: object) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    return all(
        item is True
        or (
            isinstance(item, dict)
            and _all_true_mapping(item)
        )
        for item in value.values()
    )


def _qualification_report(
    root: Path,
    path: Path,
    task: JSONObject,
    *,
    probe: str,
    verifier_outcome: str,
) -> tuple[JSONObject, str, str]:
    report_file = _artifact(root, path)
    report, report_digest = _object_with_digest(report_file)
    is_v2 = (
        task.get("schema_version") == "omp.diagnostic-task/v2"
    )
    if report.get("schema_version") != "omp.worker-run-report/v1":
        raise TaskAdmissionError(
            "qualification evidence is not a worker run report"
        )
    observation = report.get("observation")
    outcome = report.get("outcome")
    if not isinstance(observation, dict) or not isinstance(outcome, dict):
        raise TaskAdmissionError(
            "qualification report lacks normalized evidence"
        )
    expected_observation_version = (
        "omp.attempt-observation/v2"
        if is_v2
        else "omp.attempt-observation/v1"
    )
    if observation.get("schema_version") != expected_observation_version:
        raise TaskAdmissionError(
            "qualification report observation schema version does not match "
            "the diagnostic task"
        )
    observation_result = validate_value(
        root,
        "attempt-observation",
        observation,
        path,
    )
    outcome_result = validate_value(
        root,
        "attempt-outcome",
        outcome,
        path,
    )
    if not observation_result.valid or not outcome_result.valid:
        raise TaskAdmissionError(
            "qualification report evidence is invalid"
        )
    try:
        expected_outcome = classify_attempt(observation)
    except AccountingError as error:
        raise TaskAdmissionError(
            "qualification report observation cannot be classified"
        ) from error
    if outcome != expected_outcome:
        raise TaskAdmissionError(
            "qualification report outcome was not derived from its observation"
        )
    lifecycle = observation.get("lifecycle")
    verifier_result = observation.get("verifier")
    digests = observation.get("digests")
    attempt = observation.get("attempt")
    isolation = report.get("isolation")
    doctor = report.get("doctor")
    runner_evidence_matches = (
        not is_v2
        or (
            isinstance(digests, dict)
            and report.get("runner_evidence_digest_sha256")
            == digests.get("runner_evidence")
        )
    )
    if (
        report.get("passed") is not True
        or report.get("external_provider_calls") != 0
        or observation.get("issues") != []
        or observation.get("evidence_use") != "admission-only"
        or not isinstance(lifecycle, dict)
        or not lifecycle
        or any(value is not True for value in lifecycle.values())
        or not isinstance(verifier_result, dict)
        or verifier_result.get("outcome") != verifier_outcome
        or verifier_result.get("result_valid") is not True
        or not isinstance(digests, dict)
        or not isinstance(attempt, dict)
        or attempt.get("attempt_id") != report.get("run_id")
        or not _all_true_mapping(isolation)
        or not isinstance(doctor, dict)
        or doctor.get("ready") is not True
    ):
        raise TaskAdmissionError(
            "qualification report is not a healthy completed run"
        )
    if (
        report.get("artifact_digest_sha256")
        != digests.get("artifact")
        or report.get("runner_evidence_digest_sha256")
        != digests.get("runner_evidence")
        or report.get("policy_digest_sha256")
        != digests.get("runtime_policy")
        or not runner_evidence_matches
    ):
        raise TaskAdmissionError(
            "qualification report envelope does not match its observation"
        )
    assets = task.get("assets")
    runner = task.get("runner")
    verifier = task.get("verifier")
    admission_agents = task.get("admission_agents")
    expected_agent = (
        admission_agents.get(probe)
        if isinstance(admission_agents, dict)
        else None
    )
    bindings_complete = (
        isinstance(assets, dict)
        and isinstance(verifier, dict)
        and (
            not is_v2
            or (
                isinstance(runner, dict)
                and isinstance(expected_agent, dict)
            )
        )
    )
    if not bindings_complete:
        raise TaskAdmissionError("task bindings are incomplete")
    public = assets.get("public")
    private = assets.get("verifier_private")
    expected: JSONObject = {
        "task": (
            task_execution_sha256(task)
            if is_v2
            else canonical_sha256(task)
        ),
        "task_public_tree": (
            public.get("digest_sha256")
            if isinstance(public, dict)
            else None
        ),
        "verifier_private_tree": (
            private.get("digest_sha256")
            if isinstance(private, dict)
            else None
        ),
        "verifier_image_config": verifier.get(
            "config_digest_sha256"
        ),
    }
    if is_v2:
        expected.update(
            {
                "agent_image": _image_digest_suffix(
                    expected_agent.get("image")
                ),
                "agent_image_config": expected_agent.get(
                    "config_digest_sha256"
                ),
                "runner_image": _image_digest_suffix(
                    runner.get("image")
                ),
                "runner_image_config": runner.get(
                    "config_digest_sha256"
                ),
                "verifier_image": _image_digest_suffix(
                    verifier.get("image")
                ),
            }
        )
    if any(
        digests.get(name) != value
        for name, value in expected.items()
    ):
        raise TaskAdmissionError(
            "qualification report task bindings do not match"
        )
    command_digest = digests.get("agent_image_config")
    if (
        not isinstance(command_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", command_digest) is None
    ):
        raise TaskAdmissionError(
            "qualification report command binding is missing"
        )
    return report, command_digest, report_digest


def _qualification_group(
    root: Path,
    paths: Sequence[Path],
    task: JSONObject,
    *,
    probe: str,
    verifier_outcome: str,
) -> tuple[list[JSONObject], str, list[JSONObject]]:
    is_v2 = (
        task.get("schema_version") == "omp.diagnostic-task/v2"
    )
    requirement = "exactly two" if is_v2 else "two to eight"
    if isinstance(paths, (str, bytes, Path)):
        raise TaskAdmissionError(
            f"each qualification probe requires {requirement} runs"
        )
    count_is_valid = (
        len(paths) == 2 if is_v2 else 2 <= len(paths) <= 8
    )
    if not count_is_valid:
        raise TaskAdmissionError(
            f"each qualification probe requires {requirement} runs"
        )
    reports: list[JSONObject] = []
    commands: list[str] = []
    references: list[JSONObject] = []
    artifact_digests: list[object] = []
    verifier_rewards: list[object] = []
    report_digests: list[str] = []
    run_ids: list[object] = []
    for raw_path in paths:
        path = Path(raw_path)
        report_file = _artifact(root, path)
        report, command, report_digest = _qualification_report(
            root,
            path,
            task,
            probe=probe,
            verifier_outcome=verifier_outcome,
        )
        reports.append(report)
        commands.append(command)
        report_digests.append(report_digest)
        run_ids.append(report.get("run_id"))
        artifact_digests.append(
            report.get("artifact_digest_sha256")
        )
        observation = report.get("observation")
        verifier = (
            observation.get("verifier")
            if isinstance(observation, dict)
            else None
        )
        verifier_rewards.append(
            verifier.get("reward")
            if isinstance(verifier, dict)
            else None
        )
        references.append(
            {
                "path": report_file.relative_to(root).as_posix(),
                "digest_sha256": report_digest,
            }
        )
    if (
        len(set(report_digests)) != len(report_digests)
        or None in run_ids
        or len(set(run_ids)) != len(run_ids)
    ):
        raise TaskAdmissionError(
            "repeated probe runs must use distinct reports and run ids"
        )
    if len(set(commands)) != 1:
        raise TaskAdmissionError(
            "repeated probe runs must use one exact agent image"
        )
    if (
        len(set(artifact_digests)) != 1
        or None in artifact_digests
        or len(set(verifier_rewards)) != 1
    ):
        raise TaskAdmissionError(
            "repeated probe artifacts and rewards must be deterministic"
        )
    return reports, commands[0], references




def generate_task_qualification(
    root: Path,
    task_path: Path,
    baseline_report_paths: Sequence[Path],
    reference_report_paths: Sequence[Path],
    tamper_report_paths: Sequence[Path],
    output_path: Path,
    *,
    reviewer: str,
    reviewed_at: str | None = None,
) -> JSONObject:
    """Generate calibration qualification from reviewed, repeated worker evidence."""

    resolved_root = Path(root).expanduser().resolve(strict=True)
    output = _destination(
        resolved_root,
        Path(output_path),
        candidate=False,
    )
    if output.exists() or output.is_symlink():
        raise TaskWorkflowError("output already exists")
    task_file = _artifact(resolved_root, Path(task_path))
    task = _object(task_file)
    if not validate_value(
        resolved_root,
        "diagnostic-task",
        task,
        task_file.relative_to(resolved_root),
    ).valid:
        raise TaskAdmissionError("diagnostic task is invalid")
    is_v2 = (
        task.get("schema_version") == "omp.diagnostic-task/v2"
    )
    authorship = task.get("authorship")
    reviews = task.get("reviews")
    if (
        not reviewer
        or not isinstance(authorship, dict)
        or reviewer == authorship.get("author")
        or not isinstance(reviews, dict)
        or set(reviews) != {
            "privacy",
            "license",
            "verifier",
            "split",
        }
    ):
        raise TaskAdmissionError(
            "task review provenance is incomplete or non-independent"
        )
    for review in reviews.values():
        if (
            not isinstance(review, dict)
            or review.get("decision") != "approved"
        ):
            raise TaskAdmissionError(
                "task has an unapproved human review"
            )
        if review.get("reviewer") == authorship.get("author"):
            raise TaskAdmissionError(
                "task review is not independent of its author"
            )
    verifier_review = reviews["verifier"]
    if reviewer != verifier_review.get("reviewer"):
        raise TaskAdmissionError(
            "admission reviewer does not match verifier review"
        )
    if (
        reviewed_at is not None
        and reviewed_at != verifier_review.get("reviewed_at")
    ):
        raise TaskAdmissionError(
            "admission review timestamp does not match verifier review"
        )
    baseline, baseline_command, baseline_evidence = (
        _qualification_group(
            resolved_root,
            baseline_report_paths,
            task,
            probe="baseline",
            verifier_outcome="rejected",
        )
    )
    reference, reference_command, reference_evidence = (
        _qualification_group(
            resolved_root,
            reference_report_paths,
            task,
            probe="reference",
            verifier_outcome="accepted",
        )
    )
    tamper, tamper_command, tamper_evidence = (
        _qualification_group(
            resolved_root,
            tamper_report_paths,
            task,
            probe="tamper",
            verifier_outcome="rejected",
        )
    )
    evidence = (
        *baseline_evidence,
        *reference_evidence,
        *tamper_evidence,
    )
    if len({item["digest_sha256"] for item in evidence}) != len(
        evidence
    ):
        raise TaskAdmissionError(
            "qualification reports must be content-distinct"
        )
    if is_v2:
        report_digests = [
            str(item["digest_sha256"])
            for item in evidence
        ]
        verifier_evidence_path = verifier_review.get("evidence_path")
        if not isinstance(verifier_evidence_path, str):
            raise TaskAdmissionError(
                "verifier review evidence path is missing"
            )
        verifier_evidence = _object(
            _artifact(resolved_root, Path(verifier_evidence_path))
        )
        if (
            verifier_evidence.get("report_digests_sha256")
            != report_digests
        ):
            raise TaskAdmissionError(
                "verifier review does not attest the exact qualification reports"
            )
    reports = (*baseline, *reference, *tamper)
    run_ids = {report.get("run_id") for report in reports}
    if None in run_ids or len(run_ids) != len(reports):
        raise TaskAdmissionError(
            "qualification report run ids must be distinct"
        )
    if len(
        {
            baseline_command,
            reference_command,
            tamper_command,
        }
    ) != 3:
        raise TaskAdmissionError(
            "baseline, reference, and tamper agents must differ"
        )
    admission_agents = task.get("admission_agents")
    expected_commands = {
        name: value.get("config_digest_sha256")
        for name, value in (
            admission_agents.items()
            if isinstance(admission_agents, dict)
            else ()
        )
        if isinstance(value, dict)
    }
    if {
        "baseline": baseline_command,
        "reference": reference_command,
        "tamper": tamper_command,
    } != expected_commands:
        raise TaskAdmissionError(
            "qualification reports do not match task admission agents"
        )
    runner = task.get("runner")
    verifier = task.get("verifier")
    assets = task.get("assets")
    agent = task.get("agent")
    if (
        not isinstance(assets, dict)
        or not isinstance(agent, dict)
        or not isinstance(verifier, dict)
        or (is_v2 and not isinstance(runner, dict))
    ):
        raise TaskAdmissionError("task container or asset mapping is incomplete")
    if is_v2:
        objective = task.get("objective")
        obs = (
            objective.get("observation")
            if isinstance(objective, dict)
            else None
        )
        artifact_kind = (
            obs.get("artifact_kind") if isinstance(obs, dict) else None
        )
        authority = (
            obs.get("authority") if isinstance(obs, dict) else None
        )
        if artifact_kind == "data-only" and authority == "host-process":
            observation_authority = "pass"
            decision = "calibration-required"
        else:
            observation_authority = "fail"
            decision = "rejected"
        qualification: JSONObject = {
            "schema_version": "omp.task-qualification/v2",
            "task_id": task["task_id"],
            "task_version": task["task_version"],
            "content_digest_sha256": task["content_digest_sha256"],
            "source_task": {
                "path": task_file.relative_to(resolved_root).as_posix(),
                "digest_sha256": canonical_sha256(task),
            },
            "reviews": reviews,
            "evaluation_provenance": {
                "reviewer_id": verifier_review["reviewer"],
                "runner_image": runner["image"],
                "runner_config_digest_sha256": runner[
                    "config_digest_sha256"
                ],
                "runner_platform": runner["platform"],
                "verifier_image": verifier["image"],
                "verifier_config_digest_sha256": verifier[
                    "config_digest_sha256"
                ],
                "verifier_platform": verifier["platform"],
                "review_digest_sha256": verifier_review[
                    "evidence_digest_sha256"
                ],
            },
            "observed_mapping": {
                "task_digest_sha256": task_execution_sha256(task),
                "public_tree_digest_sha256": assets["public"][
                    "digest_sha256"
                ],
                "verifier_private_tree_digest_sha256": assets[
                    "verifier_private"
                ]["digest_sha256"],
                "agent_config_digest_sha256": agent[
                    "config_digest_sha256"
                ],
                "runner_config_digest_sha256": runner[
                    "config_digest_sha256"
                ],
                "verifier_config_digest_sha256": verifier[
                    "config_digest_sha256"
                ],
            },
            "checks": {
                "privacy": "pass",
                "license": "pass",
                "baseline_fails": {
                    "result": "pass",
                    "command_digest_sha256": baseline_command,
                    "evidence": baseline_evidence,
                },
                "reference_passes": {
                    "result": "pass",
                    "command_digest_sha256": reference_command,
                    "evidence": reference_evidence,
                },
                "runner_isolation": "pass",
                "verifier_isolation": "pass",
                "observation_authority": observation_authority,
                "tamper_resistance": {
                    "result": "pass",
                    "command_digest_sha256": tamper_command,
                    "evidence": tamper_evidence,
                },
                "determinism": "pass",
                "infrastructure_classification": "pass",
                "discrimination": "calibration-required",
            },
            "decision": decision,
        }
    else:
        qualification = {
            "schema_version": "omp.task-qualification/v1",
            "task_id": task["task_id"],
            "task_version": task["task_version"],
            "content_digest_sha256": task["content_digest_sha256"],
            "source_task": {
                "path": task_file.relative_to(resolved_root).as_posix(),
                "digest_sha256": canonical_sha256(task),
            },
            "reviews": reviews,
            "verifier_provenance": {
                "reviewer_id": verifier_review["reviewer"],
                "verifier_image": verifier["image"],
                "verifier_config_digest_sha256": verifier[
                    "config_digest_sha256"
                ],
                "verifier_platform": verifier["platform"],
                "review_digest_sha256": verifier_review[
                    "evidence_digest_sha256"
                ],
            },
            "observed_mapping": {
                "task_digest_sha256": canonical_sha256(task),
                "public_tree_digest_sha256": assets["public"][
                    "digest_sha256"
                ],
                "verifier_private_tree_digest_sha256": assets[
                    "verifier_private"
                ]["digest_sha256"],
                "agent_config_digest_sha256": agent[
                    "config_digest_sha256"
                ],
                "verifier_config_digest_sha256": verifier[
                    "config_digest_sha256"
                ],
            },
            "checks": {
                "privacy": "pass",
                "license": "pass",
                "baseline_fails": {
                    "result": "pass",
                    "command_digest_sha256": baseline_command,
                    "evidence": baseline_evidence,
                },
                "reference_passes": {
                    "result": "pass",
                    "command_digest_sha256": reference_command,
                    "evidence": reference_evidence,
                },
                "verifier_isolation": "pass",
                "tamper_resistance": {
                    "result": "pass",
                    "command_digest_sha256": tamper_command,
                    "evidence": tamper_evidence,
                },
                "determinism": "pass",
                "infrastructure_classification": "pass",
                "discrimination": "calibration-required",
            },
            "decision": "calibration-required",
        }
    if not validate_value(
        resolved_root,
        "task-qualification",
        qualification,
        output,
    ).valid:
        raise TaskWorkflowError(
            "generated qualification failed contract validation"
        )
    _atomic_json(resolved_root, output, qualification)
    return qualification

def _prepare_bound_manifest(
    root: Path,
    task: JSONObject,
    agent: JSONObject,
    run_id: str,
    output_path: Path,
    *,
    evidence_use: str,
    qualification: JSONObject | None,
    docker: str,
) -> JSONObject:
    manifest_output = _destination(
        root,
        Path(output_path),
        candidate=False,
    )
    if manifest_output.exists() or manifest_output.is_symlink():
        raise TaskWorkflowError("output already exists")
    is_v2 = (
        task.get("schema_version") == "omp.diagnostic-task/v2"
    )
    if task.get("partition") == "holdout":
        raise TaskAdmissionError("holdout tasks cannot be prepared")
    if task.get("routing_eligible") is not False:
        raise TaskAdmissionError(
            "diagnostic task must be explicitly routing-ineligible"
        )
    assets = task.get("assets")
    runner = task.get("runner")
    verifier = task.get("verifier")
    if (
        not isinstance(assets, dict)
        or not isinstance(verifier, dict)
        or (is_v2 and not isinstance(runner, dict))
    ):
        raise TaskAdmissionError(
            "task execution bindings are incomplete"
        )
    public = assets.get("public")
    verifier_private = assets.get("verifier_private")
    if not isinstance(public, dict) or not isinstance(
        verifier_private,
        dict,
    ):
        raise TaskAdmissionError(
            "task content bindings are incomplete"
        )
    public_digest = public.get("digest_sha256")
    private_digest = verifier_private.get("digest_sha256")
    if (
        not isinstance(public_digest, str)
        or agent.get("asset_tree_digest_sha256") != public_digest
    ):
        raise TaskAdmissionError(
            "agent image is not bound to public content"
        )
    if (
        is_v2
        and (
            not isinstance(public_digest, str)
            or runner.get("asset_tree_digest_sha256")
            != public_digest
        )
    ):
        raise TaskAdmissionError(
            "runner image is not bound to public content"
        )
    if (
        not isinstance(private_digest, str)
        or verifier.get("asset_tree_digest_sha256")
        != private_digest
    ):
        raise TaskAdmissionError(
            "verifier image is not bound to private content"
        )
    if is_v2 and len(
        {
            agent.get("config_digest_sha256"),
            runner.get("config_digest_sha256"),
            verifier.get("config_digest_sha256"),
        }
    ) != 3:
        raise TaskAdmissionError(
            "manifest container config digests must be pairwise distinct"
        )
    task_digest = (
        task_execution_sha256(task)
        if is_v2
        else canonical_sha256(task)
    )
    agent_manifest = _inspect_image(
        docker,
        agent,
        task["role"],
        public_digest,
        private_digest,
        stage="agent",
        include_stage_label=is_v2,
    )
    runner_manifest = (
        _inspect_image(
            docker,
            runner,
            task["role"],
            public_digest,
            private_digest,
            stage="runner",
            include_stage_label=True,
        )
        if is_v2
        else None
    )
    verifier_manifest = _inspect_image(
        docker,
        verifier,
        task["role"],
        public_digest,
        private_digest,
        stage="verifier",
        include_stage_label=is_v2,
    )
    captured_public_agent = _container_tree_digest(
        docker,
        str(agent["image"]),
        "/opt/rolebench/task/public",
        must_exist=True,
    )
    _container_tree_digest(
        docker,
        str(agent["image"]),
        "/opt/rolebench/task/verifier-private",
        must_exist=False,
    )
    captured_public_runner = None
    if is_v2:
        captured_public_runner = _container_tree_digest(
            docker,
            str(runner["image"]),
            "/opt/rolebench/task/public",
            must_exist=True,
        )
        _container_tree_digest(
            docker,
            str(runner["image"]),
            "/opt/rolebench/task/verifier-private",
            must_exist=False,
        )
    captured_private = _container_tree_digest(
        docker,
        str(verifier["image"]),
        "/opt/rolebench/task/verifier-private",
        must_exist=True,
    )
    _container_tree_digest(
        docker,
        str(verifier["image"]),
        "/opt/rolebench/task/public",
        must_exist=False,
    )
    if (
        captured_public_agent != public_digest
        or captured_private != private_digest
        or (
            is_v2
            and captured_public_runner != public_digest
        )
    ):
        raise TaskAdmissionError(
            "container content does not match task digests"
        )
    task_binding: JSONObject = {
        "digest_sha256": task_digest,
        "public_tree_digest_sha256": public_digest,
        "verifier_private_tree_digest_sha256": private_digest,
        "evidence_use": evidence_use,
    }
    if qualification is not None:
        task_binding["qualification_digest_sha256"] = (
            canonical_sha256(qualification)
        )
    manifest: JSONObject = {
        "schema_version": (
            "omp.worker-run-manifest/v2"
            if is_v2
            else "omp.worker-run-manifest/v1"
        ),
        "run_id": run_id,
        "role": task["role"],
        "task": task_binding,
        "policy": task["policy"],
        "provider": {"enabled": False},
        "agent": agent_manifest,
        "verifier": verifier_manifest,
    }
    if is_v2:
        manifest["runner"] = runner_manifest
    if not validate_value(
        root,
        "worker-run-manifest",
        manifest,
        manifest_output,
    ).valid:
        raise TaskWorkflowError(
            "generated manifest failed contract validation"
        )
    _atomic_json(root, manifest_output, manifest)
    return manifest


def prepare_admission_worker_manifest(
    root: Path,
    task_path: Path,
    probe: str,
    run_id: str,
    output_path: Path,
    *,
    docker: str = "docker",
) -> JSONObject:
    """Prepare a baseline, reference, or tamper admission-only run."""

    if probe not in {"baseline", "reference", "tamper"}:
        raise TaskWorkflowError("unknown admission probe")
    resolved_root = Path(root).expanduser().resolve(strict=True)
    task_file = _artifact(resolved_root, Path(task_path))
    task = _object(task_file)
    if not validate_value(
        resolved_root,
        "diagnostic-task",
        task,
        task_file.relative_to(resolved_root),
    ).valid:
        raise TaskAdmissionError("diagnostic task is invalid")
    admission_agents = task.get("admission_agents")
    agent = (
        admission_agents.get(probe)
        if isinstance(admission_agents, dict)
        else None
    )
    if not isinstance(agent, dict):
        raise TaskAdmissionError(
            "task admission agent mapping is incomplete"
        )
    return _prepare_bound_manifest(
        resolved_root,
        task,
        agent,
        run_id,
        output_path,
        evidence_use="admission-only",
        qualification=None,
        docker=docker,
    )


def prepare_worker_manifest(
    root: Path,
    task_path: Path,
    qualification_path: Path,
    run_id: str,
    output_path: Path,
    *,
    docker: str = "docker",
) -> JSONObject:
    """Prepare a routing-ineligible calibration run."""

    resolved_root = Path(root).expanduser().resolve(strict=True)
    task_file = _artifact(resolved_root, Path(task_path))
    qualification_file = _artifact(
        resolved_root,
        Path(qualification_path),
    )
    task = _object(task_file)
    qualification = _object(qualification_file)
    checked = _check_task_qualification_values(
        resolved_root,
        task_file,
        qualification_file,
        task,
        qualification,
    )
    if not checked["valid"]:
        raise TaskAdmissionError(
            "task and qualification are not a valid exact pair"
        )
    if checked["decision"] != "calibration-required":
        raise TaskAdmissionError(
            "task qualification is rejected"
        )
    agent = task.get("agent")
    if not isinstance(agent, dict):
        raise TaskAdmissionError(
            "task agent mapping is incomplete"
        )
    return _prepare_bound_manifest(
        resolved_root,
        task,
        agent,
        run_id,
        output_path,
        evidence_use="calibration-only",
        qualification=qualification,
        docker=docker,
    )


def verify_task_pack(root: Path, pack_path: Path) -> JSONObject:
    """Validate a pack and every digest-pinned exact task/qualification pair."""

    resolved_root = Path(root).expanduser().resolve(strict=True)
    pack_file = _artifact(resolved_root, Path(pack_path))
    pack = _object(pack_file)
    validation = validate_value(
        resolved_root,
        "task-pack",
        pack,
        pack_file.relative_to(resolved_root),
    )
    diagnostics = _diagnostics(validation)
    entries = pack.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    for ordinal, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        task_ref = entry.get("task")
        qualification_ref = entry.get("qualification")
        if not isinstance(task_ref, dict) or not isinstance(
            qualification_ref,
            dict,
        ):
            continue
        task_path_value = task_ref.get("path")
        qualification_path_value = qualification_ref.get("path")
        if not isinstance(task_path_value, str) or not isinstance(
            qualification_path_value,
            str,
        ):
            continue
        task_file = _artifact(
            resolved_root,
            Path(task_path_value),
        )
        qualification_file = _artifact(
            resolved_root,
            Path(qualification_path_value),
        )
        task = _object(task_file)
        qualification = _object(qualification_file)
        if canonical_sha256(task) != task_ref.get(
            "digest_sha256"
        ):
            diagnostics.append(
                f"entries[{ordinal}] task digest mismatch"
            )
        if canonical_sha256(qualification) != qualification_ref.get(
            "digest_sha256"
        ):
            diagnostics.append(
                f"entries[{ordinal}] qualification digest mismatch"
            )
        checked = _check_task_qualification_values(
            resolved_root,
            task_file,
            qualification_file,
            task,
            qualification,
        )
        diagnostics.extend(f"entries[{ordinal}]: {message}" for message in checked["diagnostics"])
        if (
            pack.get("status") == "frozen"
            and qualification.get("schema_version")
            == "omp.task-qualification/v1"
        ):
            diagnostics.append(
                f"entries[{ordinal}] v1 qualifications cannot freeze a task pack"
            )
        elif checked["decision"] != "calibration-required":
            diagnostics.append(
                f"entries[{ordinal}] qualification decision is not allowed"
            )
    return {"valid": not diagnostics, "diagnostics": sorted(set(diagnostics))}
