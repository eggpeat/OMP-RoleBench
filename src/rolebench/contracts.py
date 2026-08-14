"""Loading, validating, and hashing role contract repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Iterable, Iterator, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from .accounting_rules import ATTEMPT_OUTCOME_RULES


type JSONScalar = None | bool | int | float | str
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

BUILTIN_ROLES: tuple[str, ...] = (
    "default",
    "smol",
    "slow",
    "vision",
    "plan",
    "designer",
    "commit",
    "tiny",
    "task",
    "advisor",
)

_SCHEMA_DIRECTORY = Path("contracts/schemas")
_REGISTRY_FILE = Path("contracts/role-registry.json")
_ROLE_DIRECTORY = Path("contracts/roles")
_ROLE_SCHEMA = "role-contract.schema.json"
_REGISTRY_SCHEMA = "role-registry.schema.json"
_SCORED_WORKER_POLICY_FILE = Path("contracts/scored-worker-policy.json")
_SCORED_WORKER_POLICY_SCHEMA = "scored-worker-policy.schema.json"
_WORKER_RUN_MANIFEST_SCHEMA = "worker-run-manifest"
_TASK_PACK_DIRECTORY = Path("contracts/task-packs")
_TASK_PACK_SCHEMA = "task-pack.schema.json"
_DIAGNOSTIC_TASK_SCHEMA = "diagnostic-task.schema.json"
_TASK_QUALIFICATION_SCHEMA = "task-qualification.schema.json"
_TASK_REVIEW_EVIDENCE_SCHEMA = "task-review-evidence.schema.json"
_EXPERIMENT_LEDGER_ENTRY_SCHEMA = "experiment-ledger-entry.schema.json"
_PILOT_TASK_PACKS: tuple[str, ...] = ("default", "task", "smol", "slow")
_VERSIONED_ARTIFACT_SCHEMAS: dict[str, dict[str, str]] = {
    "attempt-observation": {
        "omp.attempt-observation/v1": "attempt-observation-v1",
        "omp.attempt-observation/v2": "attempt-observation",
    },
    "attempt-outcome": {
        "omp.attempt-outcome/v1": "attempt-outcome-v1",
        "omp.attempt-outcome/v2": "attempt-outcome",
    },
    "diagnostic-task": {
        "omp.diagnostic-task/v1": "diagnostic-task-v1",
        "omp.diagnostic-task/v2": "diagnostic-task",
    },
    "task-qualification": {
        "omp.task-qualification/v1": "task-qualification-v1",
        "omp.task-qualification/v2": "task-qualification",
    },
    "scored-worker-policy": {
        "omp.scored-worker-policy/v1": "scored-worker-policy-v1",
        "omp.scored-worker-policy/v2": "scored-worker-policy",
    },
    "worker-run-manifest": {
        "omp.worker-run-manifest/v1": "worker-run-manifest-v1",
        "omp.worker-run-manifest/v2": "worker-run-manifest",
    },
}
_DIGEST_CHUNK_SIZE = 1024 * 1024
_MAX_DIGEST_FILE_BYTES = 64 * 1024 * 1024
_MAX_DIGEST_TREE_FILES = 10_000
_MAX_DIGEST_TREE_BYTES = 512 * 1024 * 1024
_MAX_DIGEST_PATH_BYTES = 4096
_MAX_DIGEST_TREE_DEPTH = 128
_THRESHOLD_VALUES = (
    "quality_floor",
    "reliability_floor",
    "confidence",
    "latency_slo_seconds",
)

_RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    """Validate the RFC 3339 subset used by RoleBench timestamps."""

    if not isinstance(value, str):
        return True
    if _RFC3339_DATETIME.fullmatch(value) is None:
        return False
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, order=True)
class Diagnostic:
    """One deterministic validation diagnostic."""

    file: str
    json_path: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """The complete validation outcome."""

    diagnostics: tuple[Diagnostic, ...]

    @property
    def valid(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class Repository:
    """Loaded registry, manifests, mapped task packs, and canonical worker policy."""

    root: Path
    registry: JSONObject
    manifests: tuple[tuple[str, JSONObject], ...]
    task_packs: tuple[tuple[str, JSONObject], ...]
    scored_worker_policy: JSONObject


@dataclass(frozen=True)
class ContractError(Exception):
    """A user-facing contract repository error."""

    message: str
    file: str | None = None
    json_path: str = "$"

    def __str__(self) -> str:
        if self.file is None:
            return self.message
        return f"{self.file}:{self.json_path}: {self.message}"


def discover_root(start: Path | None = None) -> Path:
    """Find the nearest ancestor containing the role registry."""

    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / _REGISTRY_FILE).is_file():
            return candidate
    raise ContractError(
        f"could not find {_REGISTRY_FILE.as_posix()} from {current} or its parents"
    )


def resolve_root(root: Path | None) -> Path:
    """Resolve an explicit root or discover one from the current directory."""

    if root is None:
        return discover_root()
    resolved = root.expanduser().resolve()
    if not (resolved / _REGISTRY_FILE).is_file():
        raise ContractError(
            f"repository root does not contain {_REGISTRY_FILE.as_posix()}: {resolved}"
        )
    return resolved


def _as_json(value: object, *, file: str) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_as_json(item, file=file) for item in value]
    if isinstance(value, dict):
        converted: JSONObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("JSON object key is not a string", file)
            converted[key] = _as_json(item, file=file)
        return converted
    raise ContractError(f"unsupported JSON value {type(value).__name__}", file)


def _load_json(root: Path, relative: Path) -> JSONValue:
    path = root / relative
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractError(str(error), relative.as_posix()) from error
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
            relative.as_posix(),
        ) from error
    return _as_json(decoded, file=relative.as_posix())


def _load_object(root: Path, relative: Path) -> JSONObject:
    value = _load_json(root, relative)
    if not isinstance(value, dict):
        raise ContractError("document must be a JSON object", relative.as_posix())
    return value


def load_repository(root: Path | None = None) -> Repository:
    """Load the registry, canonical role manifests, mapped task packs, and worker policy."""

    resolved = resolve_root(root)
    registry = _load_object(resolved, _REGISTRY_FILE)
    contracts = registry.get("contracts")
    if not isinstance(contracts, dict):
        raise ContractError("contracts must be an object", _REGISTRY_FILE.as_posix(), "$.contracts")

    manifests: list[tuple[str, JSONObject]] = []
    for role in BUILTIN_ROLES:
        relative_value = contracts.get(role)
        if not isinstance(relative_value, str):
            raise ContractError(
                f"missing contract path for role {role!r}",
                _REGISTRY_FILE.as_posix(),
                f"$.contracts.{role}",
            )
        manifests.append((role, _load_object(resolved, Path(relative_value))))

    pack_map = registry.get("task_packs")
    if not isinstance(pack_map, dict):
        raise ContractError("task_packs must be an object", _REGISTRY_FILE.as_posix(), "$.task_packs")
    task_packs: list[tuple[str, JSONObject]] = []
    for role in _PILOT_TASK_PACKS:
        relative_value = pack_map.get(role)
        if not isinstance(relative_value, str):
            raise ContractError(
                f"missing task pack path for role {role!r}",
                _REGISTRY_FILE.as_posix(),
                f"$.task_packs.{role}",
            )
        task_packs.append((role, _load_object(resolved, Path(relative_value))))

    policy = _load_object(resolved, _SCORED_WORKER_POLICY_FILE)
    return Repository(resolved, registry, tuple(manifests), tuple(task_packs), policy)


def canonical_json(value: JSONValue) -> str:
    """Encode JSON in the stable form used for display and hashing."""

    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

def canonical_sha256(value: JSONValue) -> str:
    """Return the SHA-256 of a value's canonical UTF-8 JSON encoding."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def task_content_sha256(
    public_tree_digest_sha256: str,
    verifier_private_tree_digest_sha256: str,
) -> str:
    """Hash the two disjoint task content trees without image references."""

    values = (
        public_tree_digest_sha256,
        verifier_private_tree_digest_sha256,
    )
    if any(
        re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in values
    ):
        raise ContractError("task content tree digests must be SHA-256")
    return canonical_sha256(
        {
            "public_tree_digest_sha256": public_tree_digest_sha256,
            "verifier_private_tree_digest_sha256": (
                verifier_private_tree_digest_sha256
            ),
        }
    )


def _digest_path(path: Path) -> Path:
    candidate = path.expanduser()
    encoded = os.fsencode(candidate)
    if not encoded or len(encoded) > _MAX_DIGEST_PATH_BYTES:
        raise ContractError(f"digest path must be between 1 and {_MAX_DIGEST_PATH_BYTES} encoded bytes")
    if b"\0" in encoded or ".." in candidate.parts:
        raise ContractError("digest path must not contain NUL or parent traversal")
    parts = candidate.parts[1:] if candidate.is_absolute() else candidate.parts
    current = Path(candidate.anchor) if candidate.is_absolute() else Path()
    for part in parts:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ContractError(f"digest path must not traverse symbolic link {current}")
        except FileNotFoundError:
            break
        except OSError as error:
            raise ContractError(f"cannot inspect digest path {current}: {error.strerror}") from error
    return candidate


def _open_nofollow(path: Path, *, directory: bool = False, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as error:
        raise ContractError(f"cannot safely open digest path {path}: {error.strerror}") from error


def _stream_regular_file(
    descriptor: int,
    digest: object,
    *,
    expected: os.stat_result,
    byte_limit: int,
) -> int:
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        raise ContractError("digest input must be a regular file")
    if (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    ) != (
        expected.st_dev,
        expected.st_ino,
        expected.st_size,
        expected.st_mtime_ns,
        expected.st_ctime_ns,
    ):
        raise ContractError("digest input changed while opening")
    if opened.st_size < 0 or opened.st_size > byte_limit:
        raise ContractError(
            f"digest file exceeds {byte_limit} byte limit"
        )
    consumed = 0
    while True:
        chunk = os.read(
            descriptor,
            min(
                _DIGEST_CHUNK_SIZE,
                byte_limit - consumed + 1,
            ),
        )
        if not chunk:
            break
        consumed += len(chunk)
        if consumed > byte_limit:
            raise ContractError(
                f"digest file exceeds {byte_limit} byte limit"
            )
        digest.update(chunk)  # type: ignore[attr-defined]
    final = os.fstat(descriptor)
    if (
        consumed != opened.st_size
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
        raise ContractError("digest input changed while reading")
    return consumed


def file_sha256(path: Path) -> str:
    """Hash one bounded regular file without following symbolic links."""

    candidate = _digest_path(path)
    try:
        metadata = candidate.lstat()
    except OSError as error:
        raise ContractError(f"cannot stat digest path {candidate}: {error.strerror}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ContractError("digest input must be a regular file, not a symlink or special file")
    descriptor = _open_nofollow(candidate)
    digest = sha256()
    try:
        _stream_regular_file(
            descriptor,
            digest,
            expected=metadata,
            byte_limit=_MAX_DIGEST_FILE_BYTES,
        )
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    """Hash a bounded regular-file tree using deterministic length-delimited records."""

    candidate = _digest_path(path)
    try:
        metadata = candidate.lstat()
    except OSError as error:
        raise ContractError(f"cannot stat digest tree {candidate}: {error.strerror}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise ContractError("tree digest input must be a directory, not a symlink or special file")

    root_fd = _open_nofollow(candidate, directory=True)
    digest = sha256(b"omp-rolebench-tree-sha256-v1\\0")
    entry_count = 0
    total_bytes = 0

    def walk(
        directory_fd: int,
        prefix: tuple[str, ...],
        depth: int,
    ) -> None:
        nonlocal entry_count, total_bytes
        try:
            with os.scandir(directory_fd) as iterator:
                entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        except OSError as error:
            raise ContractError(f"cannot scan digest tree: {error.strerror}") from error
        for entry in entries:
            name = entry.name
            if not name or name in {".", ".."} or "/" in name or "\0" in name:
                raise ContractError("tree contains an unsafe path component")
            entry_count += 1
            if (
                entry_count > _MAX_DIGEST_TREE_FILES
                or depth + 1 > _MAX_DIGEST_TREE_DEPTH
            ):
                raise ContractError(
                    "tree exceeds structural limits"
                )
            relative_parts = (*prefix, name)
            relative_text = "/".join(relative_parts)
            relative_bytes = relative_text.encode("utf-8")
            if len(relative_bytes) > _MAX_DIGEST_PATH_BYTES:
                raise ContractError(f"tree path exceeds {_MAX_DIGEST_PATH_BYTES} encoded bytes")
            try:
                item = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ContractError(f"cannot stat tree entry {relative_text}: {error.strerror}") from error
            if stat.S_ISLNK(item.st_mode):
                raise ContractError(f"tree contains symbolic link {relative_text}")
            if stat.S_ISDIR(item.st_mode):
                child_fd = _open_nofollow(Path(name), directory=True, dir_fd=directory_fd)
                try:
                    opened_child = os.fstat(child_fd)
                    if (
                        opened_child.st_dev,
                        opened_child.st_ino,
                        opened_child.st_mtime_ns,
                        opened_child.st_ctime_ns,
                    ) != (
                        item.st_dev,
                        item.st_ino,
                        item.st_mtime_ns,
                        item.st_ctime_ns,
                    ):
                        raise ContractError(
                            f"tree directory changed while opening {relative_text}"
                        )
                    walk(child_fd, relative_parts, depth + 1)
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
                        raise ContractError(
                            f"tree directory changed while reading {relative_text}"
                        )
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(item.st_mode):
                raise ContractError(f"tree contains special file {relative_text}")
            if item.st_size < 0 or item.st_size > _MAX_DIGEST_FILE_BYTES:
                raise ContractError(f"tree file {relative_text} exceeds {_MAX_DIGEST_FILE_BYTES} byte limit")
            if total_bytes + item.st_size > _MAX_DIGEST_TREE_BYTES:
                raise ContractError(f"tree exceeds {_MAX_DIGEST_TREE_BYTES} total byte limit")
            descriptor = _open_nofollow(Path(name), dir_fd=directory_fd)
            digest.update(b"F")
            digest.update(len(relative_bytes).to_bytes(8, "big"))
            digest.update(relative_bytes)
            digest.update(b"\\1" if item.st_mode & 0o111 else b"\\0")
            digest.update(item.st_size.to_bytes(8, "big"))
            try:
                consumed = _stream_regular_file(
                    descriptor,
                    digest,
                    expected=item,
                    byte_limit=_MAX_DIGEST_FILE_BYTES,
                )
            finally:
                os.close(descriptor)
            if total_bytes + consumed > _MAX_DIGEST_TREE_BYTES:
                raise ContractError(f"tree exceeds {_MAX_DIGEST_TREE_BYTES} total byte limit")
            total_bytes += consumed

    try:
        opened_root = os.fstat(root_fd)
        if (
            opened_root.st_dev,
            opened_root.st_ino,
            opened_root.st_mtime_ns,
            opened_root.st_ctime_ns,
        ) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ):
            raise ContractError("digest tree changed while opening")
        walk(root_fd, (), 0)
        closed_root = os.fstat(root_fd)
        if (
            closed_root.st_dev,
            closed_root.st_ino,
            closed_root.st_mtime_ns,
            closed_root.st_ctime_ns,
        ) != (
            opened_root.st_dev,
            opened_root.st_ino,
            opened_root.st_mtime_ns,
            opened_root.st_ctime_ns,
        ):
            raise ContractError("digest tree changed while reading")
    finally:
        os.close(root_fd)
    return digest.hexdigest()


def canonical_digest(repository: Repository) -> str:
    """Hash every canonical registry document in deterministic registry order."""

    documents: tuple[JSONObject, ...] = (
        repository.registry,
        *(manifest for _, manifest in repository.manifests),
        *(pack for _, pack in repository.task_packs),
        repository.scored_worker_policy,
    )
    digest = sha256()
    for document in documents:
        encoded = canonical_json(document).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def _json_path(parts: Iterable[object]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            name = str(part)
            if name.isidentifier():
                path += f".{name}"
            else:
                path += f"[{json.dumps(name, ensure_ascii=False)}]"
    return path


def _schema_diagnostics(schema: JSONObject, relative: Path) -> tuple[Diagnostic, ...]:
    validator = Draft202012Validator(
        Draft202012Validator.META_SCHEMA,
        format_checker=_FORMAT_CHECKER,
    )
    errors = sorted(
        validator.iter_errors(schema),
        key=lambda item: (
            tuple(str(part) for part in item.absolute_path),
            tuple(str(part) for part in item.absolute_schema_path),
            item.message,
        ),
    )
    return tuple(
        Diagnostic(relative.as_posix(), _json_path(error.absolute_path), error.message)
        for error in errors
    )


def _instance_diagnostics(
    instance: JSONObject,
    schema: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda item: (
            tuple(str(part) for part in item.absolute_path),
            tuple(str(part) for part in item.absolute_schema_path),
            item.message,
        ),
    )
    for error in errors:
        yield Diagnostic(relative.as_posix(), _json_path(error.absolute_path), error.message)


def _duplicate_diagnostics(value: JSONValue, relative: Path, parts: tuple[object, ...] = ()) -> Iterator[Diagnostic]:
    if isinstance(value, list):
        seen: dict[str, int] = {}
        for index, item in enumerate(value):
            fingerprint = canonical_json(item)
            first = seen.get(fingerprint)
            if first is not None:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path((*parts, index)),
                    f"duplicate array item; first appears at index {first}",
                )
            else:
                seen[fingerprint] = index
            yield from _duplicate_diagnostics(item, relative, (*parts, index))
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _duplicate_diagnostics(value[key], relative, (*parts, key))


def _registry_semantics(registry: JSONObject) -> Iterator[Diagnostic]:
    relative = _REGISTRY_FILE
    task_packs = registry.get("task_packs")
    if isinstance(task_packs, dict):
        actual = set(task_packs)
        expected = set(_PILOT_TASK_PACKS)
        if actual != expected:
            yield Diagnostic(
                relative.as_posix(),
                "$.task_packs",
                f"task pack coverage mismatch; missing={sorted(expected - actual)!r}, extra={sorted(actual - expected)!r}",
            )
        for role in _PILOT_TASK_PACKS:
            expected_path = (_TASK_PACK_DIRECTORY / f"{role}-v1.json").as_posix()
            actual_path = task_packs.get(role)
            if actual_path is not None and actual_path != expected_path:
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.task_packs.{role}",
                    f"must be {expected_path!r}",
                )
    roles = registry.get("roles")
    if isinstance(roles, list) and roles != list(BUILTIN_ROLES):
        yield Diagnostic(
            relative.as_posix(),
            "$.roles",
            f"roles must exactly equal {list(BUILTIN_ROLES)!r}",
        )
    contracts = registry.get("contracts")
    if isinstance(contracts, dict):
        actual = set(contracts)
        expected = set(BUILTIN_ROLES)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            yield Diagnostic(
                relative.as_posix(),
                "$.contracts",
                f"contract coverage mismatch; missing={missing!r}, extra={extra!r}",
            )
        for role in BUILTIN_ROLES:
            expected_path = (_ROLE_DIRECTORY / f"{role}.json").as_posix()
            actual_path = contracts.get(role)
            if actual_path is not None and actual_path != expected_path:
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.contracts.{role}",
                    f"must be {expected_path!r}",
                )
    yield from _duplicate_diagnostics(registry, relative)


def _manifest_semantics(
    role: str,
    manifest: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    declared_role = manifest.get("role")
    if declared_role != role:
        yield Diagnostic(
            relative.as_posix(),
            "$.role",
            f"role must match filename role {role!r}",
        )

    thresholds = manifest.get("thresholds")
    if isinstance(thresholds, dict):
        status = thresholds.get("status")
        values = [thresholds.get(name) for name in _THRESHOLD_VALUES]
        if status == "calibration-required":
            for name, value in zip(_THRESHOLD_VALUES, values, strict=True):
                if value is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        f"$.thresholds.{name}",
                        "must be null while threshold status is calibration-required",
                    )
        elif isinstance(status, str):
            for name, value in zip(_THRESHOLD_VALUES, values, strict=True):
                if value is None:
                    yield Diagnostic(
                        relative.as_posix(),
                        f"$.thresholds.{name}",
                        f"must be set while threshold status is {status!r}",
                    )
    yield from _duplicate_diagnostics(manifest, relative)

def _parse_datetime(value: JSONValue) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _route_policy_semantics(
    policy: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    roles = policy.get("roles")
    if not isinstance(roles, dict):
        return
    if not roles:
        yield Diagnostic(relative.as_posix(), "$.roles", "must contain at least one active role")
    for role in sorted(roles):
        if role not in BUILTIN_ROLES:
            yield Diagnostic(
                relative.as_posix(),
                _json_path(("roles", role)),
                f"unknown role {role!r}",
            )
            continue
        role_policy = roles[role]
        if not isinstance(role_policy, dict):
            continue
        routes = role_policy.get("routes")
        if isinstance(routes, list):
            route_ids: dict[str, int] = {}
            total = 0
            complete_weights = True
            for index, route in enumerate(routes):
                if not isinstance(route, dict):
                    complete_weights = False
                    continue
                route_id = route.get("route_id")
                if isinstance(route_id, str):
                    previous = route_ids.get(route_id)
                    if previous is not None:
                        yield Diagnostic(
                            relative.as_posix(),
                            _json_path(("roles", role, "routes", index, "route_id")),
                            f"duplicate route_id; first appears at index {previous}",
                        )
                    else:
                        route_ids[route_id] = index
                weight = route.get("weight_bps")
                if isinstance(weight, int) and not isinstance(weight, bool):
                    total += weight
                    if weight <= 0:
                        yield Diagnostic(
                            relative.as_posix(),
                            _json_path(("roles", role, "routes", index, "weight_bps")),
                            "weight_bps must be positive",
                        )
                else:
                    complete_weights = False
            if complete_weights and total != 10_000:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("roles", role, "routes")),
                    f"weight_bps values must sum to 10000; got {total}",
                )

        emergency = role_policy.get("emergency_fallback")
        if isinstance(emergency, list):
            seen_fallbacks: dict[str, int] = {}
            for index, route_id in enumerate(emergency):
                if not isinstance(route_id, str):
                    continue
                previous = seen_fallbacks.get(route_id)
                if previous is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        _json_path(("roles", role, "emergency_fallback", index)),
                        f"duplicate emergency fallback; first appears at index {previous}",
                    )
                else:
                    seen_fallbacks[route_id] = index

    created_at = _parse_datetime(policy.get("created_at"))
    valid_from = _parse_datetime(policy.get("valid_from"))
    valid_until = _parse_datetime(policy.get("valid_until"))
    try:
        created_after_start = (
            created_at is not None
            and valid_from is not None
            and created_at > valid_from
        )
        end_not_after_start = (
            valid_from is not None
            and valid_until is not None
            and valid_from >= valid_until
        )
    except TypeError:
        created_after_start = False
        end_not_after_start = False
    if created_after_start:
        yield Diagnostic(
            relative.as_posix(),
            "$.valid_from",
            "valid_from must not precede created_at",
        )
    if end_not_after_start:
        yield Diagnostic(
            relative.as_posix(),
            "$.valid_until",
            "valid_until must be later than valid_from",
        )


def _routing_decision_semantics(
    decision: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    candidates = decision.get("candidates")
    eligible_route_ids: set[str] = set()
    candidate_route_ids: dict[str, int] = {}
    ranks: dict[int, int] = {}
    if isinstance(candidates, list):
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            route_id = candidate.get("route_id")
            eligible = candidate.get("eligible")
            rank = candidate.get("rank")
            if isinstance(route_id, str):
                previous_route = candidate_route_ids.get(route_id)
                if previous_route is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        _json_path(("candidates", index, "route_id")),
                        f"duplicate candidate route_id; first appears at index {previous_route}",
                    )
                else:
                    candidate_route_ids[route_id] = index
            if eligible is True and isinstance(route_id, str):
                eligible_route_ids.add(route_id)
            if eligible is True and (
                not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("candidates", index, "rank")),
                    "eligible candidate rank must be a positive integer",
                )
            if isinstance(rank, int) and not isinstance(rank, bool) and rank > 0:
                previous = ranks.get(rank)
                if previous is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        _json_path(("candidates", index, "rank")),
                        f"duplicate candidate rank; first appears at index {previous}",
                    )
                else:
                    ranks[rank] = index

    selected_route = decision.get("selected_route")
    override = decision.get("explicit_override")
    override_active = isinstance(override, dict) and override.get("active") is True
    if override_active:
        override_route = override.get("route_id")
        if selected_route != override_route:
            yield Diagnostic(
                relative.as_posix(),
                "$.selected_route",
                "selected_route must equal explicit_override.route_id while override is active",
            )
    elif isinstance(selected_route, str) and selected_route not in eligible_route_ids:
        yield Diagnostic(
            relative.as_posix(),
            "$.selected_route",
            "selected_route must identify an eligible candidate unless explicit override is active",
        )
    elif selected_route is None and eligible_route_ids:
        yield Diagnostic(
            relative.as_posix(),
            "$.selected_route",
            "selected_route may be null only when no candidate is eligible",
        )

    fallback_ranking = decision.get("fallback_ranking")
    if isinstance(fallback_ranking, list):
        seen: dict[str, int] = {}
        for index, route_id in enumerate(fallback_ranking):
            if not isinstance(route_id, str):
                continue
            previous = seen.get(route_id)
            if previous is not None:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("fallback_ranking", index)),
                    f"duplicate fallback route; first appears at index {previous}",
                )
            else:
                seen[route_id] = index


def _attempt_observation_semantics(
    observation: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    version_v2 = (
        observation.get("schema_version")
        == "omp.attempt-observation/v2"
    )
    lifecycle = observation.get("lifecycle")
    if isinstance(lifecycle, dict):
        implications = (
            (
                ("agent_started", "environment_started"),
                ("agent_finished", "agent_started"),
                ("artifact_frozen", "agent_finished"),
                ("runner_started", "artifact_frozen"),
                ("runner_finished", "runner_started"),
                ("runner_evidence_frozen", "runner_finished"),
                ("verifier_started", "runner_evidence_frozen"),
                ("verifier_finished", "verifier_started"),
            )
            if version_v2
            else (
                ("agent_started", "environment_started"),
                ("agent_finished", "agent_started"),
                ("artifact_frozen", "agent_finished"),
                ("verifier_started", "artifact_frozen"),
                ("verifier_finished", "verifier_started"),
            )
        )
        for later, earlier in implications:
            if lifecycle.get(later) is True and lifecycle.get(earlier) is not True:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("lifecycle", later)),
                    f"{later} requires {earlier}",
                )
        verifier = observation.get("verifier")
        if isinstance(verifier, dict):
            verifier_outcome = verifier.get("outcome")
            if verifier_outcome in {"accepted", "rejected"} and lifecycle.get(
                "verifier_finished"
            ) is not True:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.verifier.outcome",
                    "a decisive verifier outcome requires a finished verifier",
                )
            if verifier_outcome == "not-run" and (
                lifecycle.get("verifier_started") is True
                or lifecycle.get("verifier_finished") is True
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    "$.verifier.outcome",
                    "not-run requires verifier_started and verifier_finished to be false",
                )

        digests = observation.get("digests")
        if isinstance(digests, dict):
            if lifecycle.get("artifact_frozen") is True and not isinstance(
                digests.get("artifact"), str
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    "$.digests.artifact",
                    "a frozen artifact requires its digest",
                )
            if (
                version_v2
                and lifecycle.get("runner_evidence_frozen") is True
                and not isinstance(digests.get("runner_evidence"), str)
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    "$.digests.runner_evidence",
                    "a frozen runner evidence requires its digest",
                )
    readiness = observation.get("readiness")
    if (
        isinstance(readiness, dict)
        and readiness.get("environment") == "ready"
        and isinstance(lifecycle, dict)
        and lifecycle.get("environment_started") is not True
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.readiness.environment",
            "a ready environment must have started",
        )

    provider = observation.get("provider")
    if isinstance(provider, dict):
        request_started = provider.get("request_started")
        http_status = provider.get("http_status")
        if (
            request_started is True
            and isinstance(lifecycle, dict)
            and lifecycle.get("agent_started") is not True
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.request_started",
                "a provider request requires a started agent",
            )
        if isinstance(http_status, int) and request_started is not True:
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.http_status",
                "an HTTP status requires a started provider request",
            )

    issues = observation.get("issues")
    issue_set = set(issues) if isinstance(issues, list) else set()

    termination = observation.get("termination")
    if isinstance(termination, dict):
        kind = termination.get("kind")
        oom_scope = termination.get("oom_scope")
        if kind == "resource-limit":
            if oom_scope not in {"attempt", "host", "unknown"}:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.termination.oom_scope",
                    "resource-limit termination requires attempt, host, or unknown scope",
                )
        elif oom_scope != "none":
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.oom_scope",
                "only resource-limit termination may have a non-none OOM scope",
            )
        verifier = observation.get("verifier")
        if (
            kind != "completed"
            and isinstance(verifier, dict)
            and verifier.get("outcome") in {"accepted", "rejected"}
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.verifier.outcome",
                "a decisive verifier outcome requires completed termination",
            )
        is_model_limit = kind == "model-deadline" or (
            kind == "resource-limit"
            and oom_scope == "attempt"
            and "runner-failure" not in issue_set
        )
        if is_model_limit and (
            not isinstance(provider, dict)
            or provider.get("request_started") is not True
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.request_started",
                "a scored model limit requires a started provider request",
            )

    integrity = observation.get("integrity")
    digests = observation.get("digests")
    if (
        isinstance(integrity, dict)
        and integrity.get("state") == "verified"
        and isinstance(digests, dict)
        and not isinstance(digests.get("trajectory"), str)
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.digests.trajectory",
            "verified integrity requires a trajectory digest",
        )

    if (
        "artifact-tampering" in issue_set
        and isinstance(integrity, dict)
        and integrity.get("state") != "failed"
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.integrity.state",
            "artifact-tampering requires failed integrity",
        )
    if isinstance(provider, dict):
        http_status = provider.get("http_status")
        expected_statuses: tuple[tuple[str, object], ...] = (
            ("provider-rate-limit", 429),
            ("provider-auth-error", {401, 403}),
        )
        for issue, expected in expected_statuses:
            if issue not in issue_set:
                continue
            matches = (
                http_status in expected
                if isinstance(expected, set)
                else http_status == expected
            )
            if not matches:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.provider.http_status",
                    f"{issue} has an inconsistent HTTP status",
                )
        if "provider-server-error" in issue_set and (
            not isinstance(http_status, int) or not 500 <= http_status <= 599
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.http_status",
                "provider-server-error requires a 5xx HTTP status",
            )


def _attempt_outcome_semantics(
    outcome: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    reason_code = outcome.get("reason_code")
    if not isinstance(reason_code, str) or reason_code not in ATTEMPT_OUTCOME_RULES:
        yield Diagnostic(
            relative.as_posix(),
            "$.reason_code",
            "reason_code has no accounting rule",
        )
        return

    (
        expected_disposition,
        allowed_domains,
        expected_model_outcome,
        expected_verifier_outcome,
        allowed_terminations,
        allowed_oom_scopes,
    ) = ATTEMPT_OUTCOME_RULES[reason_code]
    expected_fields: tuple[tuple[str, JSONValue, object, str], ...] = (
        (
            "disposition",
            outcome.get("disposition"),
            expected_disposition,
            f"must be {expected_disposition!r} for reason_code {reason_code!r}",
        ),
        (
            "model_outcome",
            outcome.get("model_outcome"),
            expected_model_outcome,
            f"must be {expected_model_outcome!r} for reason_code {reason_code!r}",
        ),
        (
            "verifier_outcome",
            outcome.get("verifier_outcome"),
            expected_verifier_outcome,
            f"must be {expected_verifier_outcome!r} for reason_code {reason_code!r}",
        ),
    )
    for field, actual, expected, message in expected_fields:
        if actual != expected:
            yield Diagnostic(
                relative.as_posix(),
                _json_path((field,)),
                message,
            )

    failure_domain = outcome.get("failure_domain")
    if failure_domain not in allowed_domains:
        yield Diagnostic(
            relative.as_posix(),
            "$.failure_domain",
            f"is incompatible with reason_code {reason_code!r}",
        )

    termination = outcome.get("termination")
    if isinstance(termination, dict):
        kind = termination.get("kind")
        oom_scope = termination.get("oom_scope")
        if kind not in allowed_terminations:
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.kind",
                f"is incompatible with reason_code {reason_code!r}",
            )
        if oom_scope not in allowed_oom_scopes:
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.oom_scope",
                f"is incompatible with reason_code {reason_code!r}",
            )
        if kind == "resource-limit":
            if oom_scope not in {"attempt", "host", "unknown"}:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.termination.oom_scope",
                    "resource-limit requires attempt, host, or unknown scope",
                )
        elif oom_scope != "none":
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.oom_scope",
                "only resource-limit may have a non-none OOM scope",
            )

    scored = expected_disposition == "scored"
    if outcome.get("valid_attempt") is not scored:
        yield Diagnostic(
            relative.as_posix(),
            "$.valid_attempt",
            f"must be {scored!r} for reason_code {reason_code!r}",
        )
    if outcome.get("counts_toward_quality") is not scored:
        yield Diagnostic(
            relative.as_posix(),
            "$.counts_toward_quality",
            f"must be {scored!r} for reason_code {reason_code!r}",
        )
    evidence_use = outcome.get("evidence_use")
    if (
        reason_code == "non-scored-evidence"
        and evidence_use
        not in {"admission-only", "calibration-only"}
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.evidence_use",
            "non-scored-evidence requires an explicit evidence use",
        )
    if evidence_use is not None and scored:
        yield Diagnostic(
            relative.as_posix(),
            "$.disposition",
            "admission and calibration evidence must not be scored",
        )


def _scored_worker_policy_semantics(
    policy: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    version_v2 = (
        policy.get("schema_version")
        == "omp.scored-worker-policy/v2"
    )
    executor = policy.get("executor")
    verifier = policy.get("verifier")
    resources = policy.get("resources")
    timeouts = policy.get("timeouts")

    worker_user = executor.get("user") if isinstance(executor, dict) else None
    verifier_user = verifier.get("user") if isinstance(verifier, dict) else None
    if isinstance(worker_user, dict) and isinstance(verifier_user, dict):
        for field in ("uid", "gid"):
            worker_id = worker_user.get(field)
            verifier_id = verifier_user.get(field)
            if (
                isinstance(worker_id, int)
                and not isinstance(worker_id, bool)
                and worker_id == verifier_id
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.verifier.user.{field}",
                    f"must differ from executor user {field}",
                )

    numeric_resources = (
        "cpu_limit",
        "memory_bytes",
        "pids_limit",
        "open_files_limit",
        "output_bytes_limit",
        "artifact_bytes_limit",
    )
    if isinstance(resources, dict):
        for field in numeric_resources:
            value = resources.get(field)
            if isinstance(value, float) and not math.isfinite(value):
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.resources.{field}",
                    "must be finite",
                )

    phase_fields = (
        (
            "setup_seconds",
            "agent_seconds",
            "artifact_seconds",
            "runner_seconds",
            "verifier_seconds",
        )
        if version_v2
        else (
            "setup_seconds",
            "agent_seconds",
            "artifact_seconds",
            "verifier_seconds",
        )
    )
    timeout_fields = (*phase_fields, "termination_grace_seconds", "total_seconds")
    if isinstance(timeouts, dict):
        finite_timeouts: dict[str, int | float] = {}
        for field in timeout_fields:
            value = timeouts.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                finite_timeouts[field] = value
            elif isinstance(value, float):
                if math.isfinite(value):
                    finite_timeouts[field] = value
                else:
                    yield Diagnostic(
                        relative.as_posix(),
                        f"$.timeouts.{field}",
                        "must be finite",
                    )

        grace = finite_timeouts.get("termination_grace_seconds")
        if grace is not None:
            for field in phase_fields:
                phase = finite_timeouts.get(field)
                if phase is not None and grace >= phase:
                    yield Diagnostic(
                        relative.as_posix(),
                        "$.timeouts.termination_grace_seconds",
                        f"must be shorter than {field}",
                    )

        if all(field in finite_timeouts for field in timeout_fields):
            minimum_total = sum(
                Fraction(finite_timeouts[field])
                for field in (*phase_fields, "termination_grace_seconds")
            )
            if Fraction(finite_timeouts["total_seconds"]) < minimum_total:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.timeouts.total_seconds",
                    "must be at least the sum of setup, agent, artifact, runner, verifier, and termination grace timeouts",
                )

    scratch = executor.get("scratch") if isinstance(executor, dict) else None
    artifact_limit = resources.get("artifact_bytes_limit") if isinstance(resources, dict) else None
    scratch_size = scratch.get("size_bytes") if isinstance(scratch, dict) else None
    if (
        isinstance(artifact_limit, int)
        and not isinstance(artifact_limit, bool)
        and isinstance(scratch_size, int)
        and not isinstance(scratch_size, bool)
        and artifact_limit > scratch_size
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.resources.artifact_bytes_limit",
            "must not exceed executor scratch size_bytes",
        )


def _worker_run_manifest_semantics(
    root: Path,
    manifest: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    role = manifest.get("role")
    if isinstance(role, str) and role not in BUILTIN_ROLES:
        yield Diagnostic(
            relative.as_posix(),
            "$.role",
            f"must be one of the built-in roles {list(BUILTIN_ROLES)!r}",
        )

    agent = manifest.get("agent")
    runner = manifest.get("runner")
    verifier = manifest.get("verifier")

    def _image_digest(container: JSONObject | None) -> str | None:
        if not isinstance(container, dict):
            return None
        image = container.get("image")
        if isinstance(image, str) and "@sha256:" in image:
            return image.rsplit("@sha256:", 1)[1]
        return None

    agent_digest = _image_digest(agent)
    runner_digest = _image_digest(runner)
    verifier_digest = _image_digest(verifier)

    if runner_digest is not None and runner_digest == agent_digest:
        yield Diagnostic(
            relative.as_posix(),
            "$.runner.image",
            "must use a different image digest from the agent",
        )
    if verifier_digest is not None and verifier_digest == agent_digest:
        yield Diagnostic(
            relative.as_posix(),
            "$.verifier.image",
            "must use a different image digest from the agent",
        )
    if verifier_digest is not None and runner_digest is not None and verifier_digest == runner_digest:
        yield Diagnostic(
            relative.as_posix(),
            "$.verifier.image",
            "must use a different image digest from the runner",
        )

    agent_config = agent.get("config_digest_sha256") if isinstance(agent, dict) else None
    runner_config = runner.get("config_digest_sha256") if isinstance(runner, dict) else None
    verifier_config = verifier.get("config_digest_sha256") if isinstance(verifier, dict) else None

    if isinstance(runner_config, str) and isinstance(agent_config, str) and runner_config == agent_config:
        yield Diagnostic(
            relative.as_posix(),
            "$.runner.config_digest_sha256",
            "must use a different config digest from the agent",
        )
    if isinstance(verifier_config, str) and isinstance(agent_config, str) and verifier_config == agent_config:
        yield Diagnostic(
            relative.as_posix(),
            "$.verifier.config_digest_sha256",
            "must use a different config digest from the agent",
        )
    if isinstance(verifier_config, str) and isinstance(runner_config, str) and verifier_config == runner_config:
        yield Diagnostic(
            relative.as_posix(),
            "$.verifier.config_digest_sha256",
            "must use a different config digest from the runner",
        )

    for container_name, container in (("agent", agent), ("runner", runner), ("verifier", verifier)):
        if not isinstance(container, dict):
            continue
        argv = container.get("argv")
        if not isinstance(argv, list):
            continue
        if not argv:
            yield Diagnostic(
                relative.as_posix(),
                f"$.{container_name}.argv",
                "must contain at least one argument",
            )
            continue
        if isinstance(argv[0], str) and not argv[0]:
            yield Diagnostic(
                relative.as_posix(),
                f"$.{container_name}.argv[0]",
                "first argument must be nonempty",
            )
        for index, argument in enumerate(argv):
            if isinstance(argument, str) and "\0" in argument:
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.{container_name}.argv[{index}]",
                    "must not contain NUL",
                )
    policy_reference = manifest.get("policy")
    if not isinstance(policy_reference, dict):
        return
    policy_path = policy_reference.get("path")
    if not isinstance(policy_path, str) or not policy_path:
        return

    candidate = Path(policy_path)
    resolved_root = root.resolve()
    unsafe = candidate.is_absolute()
    if not unsafe:
        candidate = (resolved_root / candidate).resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            unsafe = True
    if unsafe:
        yield Diagnostic(
            relative.as_posix(),
            "$.policy.path",
            "must be a relative path that resolves within the repository",
        )
        return

    policy_relative = candidate.relative_to(resolved_root)
    try:
        policy = _load_artifact(candidate, policy_relative)
    except ContractError as error:
        yield Diagnostic(
            error.file or policy_relative.as_posix(),
            error.json_path,
            error.message,
        )
        return

    policy_schema_name = _schema_name_for_artifact(
        "scored-worker-policy",
        policy,
    )
    policy_schema_relative = (
        _SCHEMA_DIRECTORY / f"{policy_schema_name}.schema.json"
    )
    policy_schema_diagnostics: list[Diagnostic] = []
    policy_schema = _safe_schema(
        resolved_root,
        policy_schema_relative,
        policy_schema_diagnostics,
    )
    yield from policy_schema_diagnostics
    if policy_schema is not None:
        yield from _schema_diagnostics(policy_schema, policy_schema_relative)
        yield from _instance_diagnostics(policy, policy_schema, policy_relative)
        yield from _scored_worker_policy_semantics(policy, policy_relative)

    declared_digest = policy_reference.get("digest_sha256")
    if isinstance(declared_digest, str):
        actual_digest = sha256(canonical_json(policy).encode("utf-8")).hexdigest()
        if declared_digest != actual_digest:
            yield Diagnostic(
                relative.as_posix(),
                "$.policy.digest_sha256",
                f"must equal canonical policy SHA-256 {actual_digest}",
            )


def _repository_path(root: Path, value: JSONValue) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = root / relative
    current = root
    try:
        for part in relative.parts:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                return None
    except OSError:
        return None
    return candidate


def _reference_object(
    root: Path,
    reference: JSONValue,
    *,
    owner: Path,
    json_path: str,
) -> tuple[Path, JSONObject] | Diagnostic:
    if not isinstance(reference, dict):
        return Diagnostic(owner.as_posix(), json_path, "must be an artifact reference")
    candidate = _repository_path(root, reference.get("path"))
    if candidate is None:
        return Diagnostic(owner.as_posix(), f"{json_path}.path", "must name a no-follow repository file")
    try:
        artifact = _load_artifact(candidate, candidate.relative_to(root))
    except ContractError as error:
        return Diagnostic(error.file or candidate.as_posix(), error.json_path, error.message)
    declared = reference.get("digest_sha256")
    actual = canonical_sha256(artifact)
    if isinstance(declared, str) and declared != actual:
        return Diagnostic(owner.as_posix(), f"{json_path}.digest_sha256", f"must equal canonical artifact SHA-256 {actual}")
    return candidate, artifact


def _asset_semantics(
    root: Path,
    asset: JSONValue,
    relative: Path,
    json_path: str,
    *,
    path_base: Path | None = None,
) -> Iterator[Diagnostic]:
    if not isinstance(asset, dict):
        return
    asset_path = asset.get("path")
    if path_base is not None and isinstance(asset_path, str):
        asset_path = (path_base / asset_path).as_posix()
    candidate = _repository_path(root, asset_path)
    if candidate is None:
        yield Diagnostic(relative.as_posix(), f"{json_path}.path", "must name a no-follow repository asset")
        return
    kind = asset.get("kind")
    try:
        actual = file_sha256(candidate) if kind == "file" else tree_sha256(candidate)
    except ContractError as error:
        yield Diagnostic(relative.as_posix(), json_path, error.message)
        return
    declared = asset.get("digest_sha256")
    if isinstance(declared, str) and declared != actual:
        yield Diagnostic(relative.as_posix(), f"{json_path}.digest_sha256", f"must equal captured {kind} SHA-256 {actual}")


def _task_candidate_semantics(root: Path, candidate: JSONObject, relative: Path) -> Iterator[Diagnostic]:
    signals = candidate.get("signals")
    entry_count = candidate.get("entry_count")
    if (
        isinstance(signals, list)
        and isinstance(entry_count, int)
        and not isinstance(entry_count, bool)
    ):
        pairs = [
            (item.get("ordinal"), item.get("kind"))
            for item in signals
            if isinstance(item, dict)
        ]
        if any(
            isinstance(ordinal, int)
            and (ordinal < 1 or ordinal > entry_count)
            for ordinal, _ in pairs
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.signals",
                "signal ordinal must name a scanned message",
            )
        if pairs != sorted(set(pairs)):
            yield Diagnostic(
                relative.as_posix(),
                "$.signals",
                "signals must be unique and canonically ordered",
            )
    assets = candidate.get("assets")
    if isinstance(assets, dict):
        for name in ("prompt", "workspace"):
            if name in assets:
                yield from _asset_semantics(
                    root,
                    assets.get(name),
                    relative,
                    f"$.assets.{name}",
                    path_base=relative.parent,
                )


def _task_review_evidence_semantics(
    root: Path,
    task: JSONObject,
    relative: Path,
    review_name: str,
    review: JSONObject,
) -> Iterator[Diagnostic]:
    json_path = f"$.reviews.{review_name}"
    reference = {
        "path": review.get("evidence_path"),
        "digest_sha256": review.get("evidence_digest_sha256"),
    }
    captured = _reference_object(
        root,
        reference,
        owner=relative,
        json_path=f"{json_path}.evidence",
    )
    if isinstance(captured, Diagnostic):
        yield captured
        return
    evidence_path, evidence = captured
    evidence_relative = evidence_path.relative_to(root)
    expected_relative = (
        relative.parent / "reviews" / f"{review_name}.json"
    )
    if evidence_relative != expected_relative:
        yield Diagnostic(
            relative.as_posix(),
            f"{json_path}.evidence_path",
            f"must be the owning task review path {expected_relative.as_posix()!r}",
        )

    evidence_schema_relative = (
        _SCHEMA_DIRECTORY / _TASK_REVIEW_EVIDENCE_SCHEMA
    )
    try:
        evidence_schema = _load_object(root, evidence_schema_relative)
    except ContractError as error:
        yield Diagnostic(
            error.file or evidence_schema_relative.as_posix(),
            error.json_path,
            error.message,
        )
    else:
        yield from _instance_diagnostics(
            evidence,
            evidence_schema,
            evidence_relative,
        )

    expected_common = {
        "task_id": task.get("task_id"),
        "task_version": task.get("task_version"),
        "review_type": review_name,
        "decision": review.get("decision"),
        "reviewer": review.get("reviewer"),
        "reviewed_at": review.get("reviewed_at"),
    }
    for field, expected in expected_common.items():
        if evidence.get(field) != expected:
            yield Diagnostic(
                evidence_relative.as_posix(),
                f"$.{field}",
                f"must match {json_path}.{field}",
            )

    scope = evidence.get("scope")
    assets = task.get("assets")
    public = assets.get("public") if isinstance(assets, dict) else None
    private = (
        assets.get("verifier_private")
        if isinstance(assets, dict)
        else None
    )
    source = task.get("source")
    runner = task.get("runner")
    verifier = task.get("verifier")
    split = task.get("split")
    family = task.get("family")

    expected_scope: dict[str, JSONValue] = {}
    if review_name == "privacy":
        prompt = public.get("prompt") if isinstance(public, dict) else None
        workspace = (
            public.get("workspace") if isinstance(public, dict) else None
        )
        verifier_asset = (
            private.get("verifier") if isinstance(private, dict) else None
        )
        expected_scope = {
            "prompt_digest_sha256": (
                prompt.get("digest_sha256")
                if isinstance(prompt, dict)
                else None
            ),
            "public_tree_digest_sha256": (
                public.get("digest_sha256")
                if isinstance(public, dict)
                else None
            ),
            "workspace_digest_sha256": (
                workspace.get("digest_sha256")
                if isinstance(workspace, dict)
                else None
            ),
            "verifier_private_tree_digest_sha256": (
                private.get("digest_sha256")
                if isinstance(private, dict)
                else None
            ),
            "verifier_digest_sha256": (
                verifier_asset.get("digest_sha256")
                if isinstance(verifier_asset, dict)
                else None
            ),
        }
    elif review_name == "license":
        expected_scope = {
            "source_task": (
                source.get("task") if isinstance(source, dict) else None
            ),
            "source_digest_sha256": (
                source.get("digest_sha256")
                if isinstance(source, dict)
                else None
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
        }
        if evidence.get("license") != task.get("license"):
            yield Diagnostic(
                evidence_relative.as_posix(),
                "$.license",
                "must match the owning task license",
            )
    elif review_name == "verifier":
        expected_scope = {
            "public_tree_digest_sha256": (
                public.get("digest_sha256")
                if isinstance(public, dict)
                else None
            ),
            "runner_image": (
                runner.get("image") if isinstance(runner, dict) else None
            ),
            "runner_config_digest_sha256": (
                runner.get("config_digest_sha256")
                if isinstance(runner, dict)
                else None
            ),
            "verifier_image": (
                verifier.get("image")
                if isinstance(verifier, dict)
                else None
            ),
            "verifier_config_digest_sha256": (
                verifier.get("config_digest_sha256")
                if isinstance(verifier, dict)
                else None
            ),
            "verifier_private_tree_digest_sha256": (
                private.get("digest_sha256")
                if isinstance(private, dict)
                else None
            ),
        }
    elif review_name == "split":
        assignment = evidence.get("assignment")
        expected_assignment = {
            "family_id": (
                family.get("family_id")
                if isinstance(family, dict)
                else None
            ),
            "partition": task.get("partition"),
            "assignment_method": (
                split.get("assignment_method")
                if isinstance(split, dict)
                else None
            ),
            "confidentiality": (
                split.get("confidentiality")
                if isinstance(split, dict)
                else None
            ),
            "role": task.get("role"),
        }
        if assignment != expected_assignment:
            yield Diagnostic(
                evidence_relative.as_posix(),
                "$.assignment",
                "must match the owning task split assignment",
            )
        if evidence.get("capability_tags") != task.get("capability_tags"):
            yield Diagnostic(
                evidence_relative.as_posix(),
                "$.capability_tags",
                "must match the owning task capability tags",
            )

    if isinstance(scope, dict):
        for field, expected in expected_scope.items():
            if scope.get(field) != expected:
                yield Diagnostic(
                    evidence_relative.as_posix(),
                    f"$.scope.{field}",
                    f"must match the owning task {review_name} scope",
                )


def _diagnostic_task_semantics(root: Path, task: JSONObject, relative: Path) -> Iterator[Diagnostic]:
    version_v2 = (
        task.get("schema_version")
        == "omp.diagnostic-task/v2"
    )
    if (
        task.get("partition") == "holdout"
        and (not relative.parts or relative.parts[0] != ".rolebench")
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.partition",
            "public repository diagnostic tasks must not contain holdout material",
        )
    role = task.get("role")
    if isinstance(role, str) and role in BUILTIN_ROLES:
        contract_relative = _ROLE_DIRECTORY / f"{role}.json"
        try:
            contract = _load_object(root, contract_relative)
        except ContractError as error:
            yield Diagnostic(error.file or contract_relative.as_posix(), error.json_path, error.message)
        else:
            binding = task.get("role_contract")
            if isinstance(binding, dict):
                if binding.get("contract_id") != contract.get("contract_id"):
                    yield Diagnostic(relative.as_posix(), "$.role_contract.contract_id", "must match the canonical role contract")
                actual = canonical_sha256(contract)
                if binding.get("digest_sha256") != actual:
                    yield Diagnostic(relative.as_posix(), "$.role_contract.digest_sha256", f"must equal canonical role-contract SHA-256 {actual}")
            if task.get("task_mix") != contract.get("task_mix"):
                yield Diagnostic(relative.as_posix(), "$.task_mix", "must match the canonical role contract")
            required = contract.get("required_capabilities")
            tags = task.get("capability_tags")
            if isinstance(required, list) and isinstance(tags, list):
                outside_contract = sorted(set(tags) - set(required))
                if outside_contract:
                    yield Diagnostic(
                        relative.as_posix(),
                        "$.capability_tags",
                        f"contains capabilities outside the role contract {outside_contract!r}",
                    )
            verifier_contract = contract.get("verifier")
            objective = task.get("objective")
            modes = verifier_contract.get("modes") if isinstance(verifier_contract, dict) else None
            if (
                isinstance(objective, dict)
                and isinstance(modes, list)
                and objective.get("mode") not in modes
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    "$.objective.mode",
                    "must be one of the canonical role verifier modes",
                )
            if isinstance(objective, dict):
                observation = objective.get("observation")
                if isinstance(observation, dict):
                    artifact_kind = observation.get("artifact_kind")
                    authority = observation.get("authority")
                    runner_output_trust = observation.get("runner_output_trust")
                    if artifact_kind == "executable" and authority != "source-separated-service":
                        yield Diagnostic(
                            relative.as_posix(),
                            "$.objective.observation.authority",
                            "executable artifacts require source-separated-service observation authority",
                        )
                    if runner_output_trust != "untrusted":
                        yield Diagnostic(
                            relative.as_posix(),
                            "$.objective.observation.runner_output_trust",
                            "runner_output_trust must be 'untrusted'",
                        )
    split = task.get("split")
    authorship = task.get("authorship")
    author = authorship.get("author") if isinstance(authorship, dict) else None
    if isinstance(split, dict):
        if split.get("author_id") != author:
            yield Diagnostic(relative.as_posix(), "$.split.author_id", "must match authorship.author")
        if split.get("reviewer_id") == author:
            yield Diagnostic(relative.as_posix(), "$.split.reviewer_id", "split reviewer must be independent from the author")
    reviews = task.get("reviews")
    if isinstance(reviews, dict):
        for name in ("privacy", "license", "verifier", "split"):
            review = reviews.get(name)
            if isinstance(review, dict) and review.get("reviewer") == author:
                yield Diagnostic(relative.as_posix(), f"$.reviews.{name}.reviewer", "reviewer must be independent from the author")
            if version_v2 and isinstance(review, dict):
                yield from _task_review_evidence_semantics(
                    root,
                    task,
                    relative,
                    name,
                    review,
                )
        license_review = reviews.get("license")
        license_value = task.get("license")
        if isinstance(license_review, dict) and isinstance(license_value, dict):
            for name in ("expression", "redistribution"):
                if license_review.get(name) != license_value.get(name):
                    yield Diagnostic(relative.as_posix(), f"$.reviews.license.{name}", "must bind the exact task license")
            if license_value.get("expression") == "NOASSERTION" or license_value.get("redistribution") != "permitted":
                yield Diagnostic(relative.as_posix(), "$.reviews.license.decision", "cannot approve NOASSERTION or non-permitted redistribution")
        verifier_review = reviews.get("verifier")
        runner = task.get("runner")
        verifier = task.get("verifier")
        assets = task.get("assets")
        private = assets.get("verifier_private") if isinstance(assets, dict) else None
        if isinstance(verifier_review, dict) and isinstance(verifier, dict):
            expected_verifier = {
                "verifier_image": verifier.get("image"),
                "verifier_config_digest_sha256": verifier.get(
                    "config_digest_sha256"
                ),
                "verifier_platform": verifier.get("platform"),
                "private_tree_digest_sha256": (
                    private.get("digest_sha256")
                    if isinstance(private, dict)
                    else None
                ),
            }
            if version_v2 and isinstance(runner, dict):
                expected_verifier.update(
                    {
                        "runner_image": runner.get("image"),
                        "runner_config_digest_sha256": runner.get(
                            "config_digest_sha256"
                        ),
                        "runner_platform": runner.get("platform"),
                    }
                )
            elif not version_v2:
                expected_verifier = {
                    "image": verifier.get("image"),
                    "config_digest_sha256": verifier.get(
                        "config_digest_sha256"
                    ),
                    "platform": verifier.get("platform"),
                    "private_tree_digest_sha256": (
                        private.get("digest_sha256")
                        if isinstance(private, dict)
                        else None
                    ),
                }
            for name, value in expected_verifier.items():
                if verifier_review.get(name) != value:
                    yield Diagnostic(
                        relative.as_posix(),
                        f"$.reviews.verifier.{name}",
                        "must bind the exact verifier execution mapping",
                    )
        split_review = reviews.get("split")
        family = task.get("family")
        if isinstance(split_review, dict) and isinstance(split, dict):
            expected_split = {
                "family_id": family.get("family_id") if isinstance(family, dict) else None,
                "partition": task.get("partition"),
                "assignment_method": split.get("assignment_method"),
                "confidentiality": split.get("confidentiality"),
            }
            for name, value in expected_split.items():
                if split_review.get(name) != value:
                    yield Diagnostic(relative.as_posix(), f"$.reviews.split.{name}", "must bind the exact family split assignment")
            if split_review.get("reviewer") != split.get("reviewer_id"):
                yield Diagnostic(relative.as_posix(), "$.reviews.split.reviewer", "must match the recorded split reviewer")
            if split_review.get("evidence_digest_sha256") != split.get("provenance_digest_sha256"):
                yield Diagnostic(relative.as_posix(), "$.reviews.split.evidence_digest_sha256", "must bind the split assignment provenance")

    policy = task.get("policy")
    if isinstance(policy, dict):
        if policy.get("path") != _SCORED_WORKER_POLICY_FILE.as_posix():
            yield Diagnostic(relative.as_posix(), "$.policy.path", f"must be {_SCORED_WORKER_POLICY_FILE.as_posix()!r}")
        try:
            canonical_policy = _load_object(root, _SCORED_WORKER_POLICY_FILE)
        except ContractError as error:
            yield Diagnostic(error.file or _SCORED_WORKER_POLICY_FILE.as_posix(), error.json_path, error.message)
        else:
            actual = canonical_sha256(canonical_policy)
            if policy.get("digest_sha256") != actual:
                yield Diagnostic(relative.as_posix(), "$.policy.digest_sha256", f"must equal canonical policy SHA-256 {actual}")

    assets = task.get("assets")
    public_digest: JSONValue = None
    private_digest: JSONValue = None
    asset_roots: dict[str, Path] = {}
    if isinstance(assets, dict):
        for group_name in ("public", "verifier_private"):
            group = assets.get(group_name)
            if not isinstance(group, dict):
                continue
            root_path = _repository_path(root, group.get("root"))
            if root_path is None:
                yield Diagnostic(relative.as_posix(), f"$.assets.{group_name}.root", "must name a no-follow repository tree")
            else:
                asset_roots[group_name] = root_path
                try:
                    actual = tree_sha256(root_path)
                except ContractError as error:
                    yield Diagnostic(relative.as_posix(), f"$.assets.{group_name}.root", error.message)
                else:
                    if group.get("digest_sha256") != actual:
                        yield Diagnostic(relative.as_posix(), f"$.assets.{group_name}.digest_sha256", f"must equal captured tree SHA-256 {actual}")
                for asset_name in ("prompt", "workspace", "verifier"):
                    asset = group.get(asset_name)
                    if not isinstance(asset, dict):
                        continue
                    asset_path = _repository_path(root, asset.get("path"))
                    if asset_path is not None:
                        try:
                            asset_path.relative_to(root_path)
                        except ValueError:
                            yield Diagnostic(relative.as_posix(), f"$.assets.{group_name}.{asset_name}.path", "must be contained by its declared asset root")
                    yield from _asset_semantics(root, asset, relative, f"$.assets.{group_name}.{asset_name}")
            if group_name == "public":
                public_digest = group.get("digest_sha256")
            else:
                private_digest = group.get("digest_sha256")
        public_root = asset_roots.get("public")
        private_root = asset_roots.get("verifier_private")
        if public_root is not None and private_root is not None:
            if public_root == private_root or public_root.is_relative_to(private_root) or private_root.is_relative_to(public_root):
                yield Diagnostic(relative.as_posix(), "$.assets.verifier_private.root", "public and verifier-private asset roots must be disjoint")
        if isinstance(public_digest, str) and public_digest == private_digest:
            yield Diagnostic(relative.as_posix(), "$.assets.verifier_private.digest_sha256", "public and verifier-private tree digests must be distinct")
        if isinstance(public_digest, str) and isinstance(
            private_digest,
            str,
        ):
            expected_content = task_content_sha256(
                public_digest,
                private_digest,
            )
            if task.get("content_digest_sha256") != expected_content:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.content_digest_sha256",
                    f"must equal canonical task-content SHA-256 {expected_content}",
                )

    agent = task.get("agent")
    runner = task.get("runner")
    verifier = task.get("verifier")
    objective = task.get("objective")

    if version_v2 and not isinstance(runner, dict):
        yield Diagnostic(relative.as_posix(), "$.runner", "diagnostic task must specify a runner container")

    if isinstance(agent, dict) and agent.get("asset_tree_digest_sha256") != public_digest:
        yield Diagnostic(relative.as_posix(), "$.agent.asset_tree_digest_sha256", "must bind the public asset tree")
    if isinstance(runner, dict) and runner.get("asset_tree_digest_sha256") != public_digest:
        yield Diagnostic(relative.as_posix(), "$.runner.asset_tree_digest_sha256", "must bind the public asset tree")
    if isinstance(verifier, dict) and verifier.get("asset_tree_digest_sha256") != private_digest:
        yield Diagnostic(relative.as_posix(), "$.verifier.asset_tree_digest_sha256", "must bind the verifier-private asset tree")

    if isinstance(agent, dict) and isinstance(runner, dict):
        if agent.get("image") == runner.get("image"):
            yield Diagnostic(relative.as_posix(), "$.runner.image", "must differ from the agent image manifest")
        if agent.get("config_digest_sha256") == runner.get("config_digest_sha256"):
            yield Diagnostic(relative.as_posix(), "$.runner.config_digest_sha256", "must differ from the agent image config")
    if isinstance(agent, dict) and isinstance(verifier, dict):
        if agent.get("image") == verifier.get("image"):
            yield Diagnostic(relative.as_posix(), "$.verifier.image", "must differ from the agent image manifest")
        if agent.get("config_digest_sha256") == verifier.get("config_digest_sha256"):
            yield Diagnostic(relative.as_posix(), "$.verifier.config_digest_sha256", "must differ from the agent image config")
    if isinstance(runner, dict) and isinstance(verifier, dict):
        if runner.get("image") == verifier.get("image"):
            yield Diagnostic(relative.as_posix(), "$.verifier.image", "must differ from the runner image manifest")
        if runner.get("config_digest_sha256") == verifier.get("config_digest_sha256"):
            yield Diagnostic(relative.as_posix(), "$.verifier.config_digest_sha256", "must differ from the runner image config")

    admission_agents = task.get("admission_agents")
    mappings: list[tuple[str, JSONObject]] = []
    if isinstance(agent, dict):
        mappings.append(("agent", agent))
    if isinstance(runner, dict):
        mappings.append(("runner", runner))
    if isinstance(verifier, dict):
        mappings.append(("verifier", verifier))
    if isinstance(admission_agents, dict):
        for name in ("baseline", "reference", "tamper"):
            candidate = admission_agents.get(name)
            if not isinstance(candidate, dict):
                continue
            mappings.append((f"admission_agents.{name}", candidate))
            if candidate.get("asset_tree_digest_sha256") != public_digest:
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.admission_agents.{name}.asset_tree_digest_sha256",
                    "must bind the public asset tree",
                )
    image_values = [
        mapping.get("image")
        for _, mapping in mappings
        if isinstance(mapping.get("image"), str)
    ]
    if len(image_values) != len(set(image_values)):
        yield Diagnostic(
            relative.as_posix(),
            "$.admission_agents",
            "all production, admission, and verifier image manifests must differ",
        )
    config_values = [
        mapping.get("config_digest_sha256")
        for _, mapping in mappings
        if isinstance(mapping.get("config_digest_sha256"), str)
    ]
    if len(config_values) != len(set(config_values)):
        yield Diagnostic(
            relative.as_posix(),
            "$.admission_agents",
            "all production, admission, and verifier image configs must differ",
        )


def _qualification_decision(qualification: JSONObject) -> str | None:
    checks = qualification.get("checks")
    if not isinstance(checks, dict):
        return None
    mandatory = [
        "privacy",
        "license",
        "baseline_fails",
        "reference_passes",
        "verifier_isolation",
        "tamper_resistance",
        "determinism",
        "infrastructure_classification",
    ]
    if (
        qualification.get("schema_version")
        == "omp.task-qualification/v2"
    ):
        mandatory.extend(("runner_isolation", "observation_authority"))
    values: list[JSONValue] = []
    for name in mandatory:
        value = checks.get(name)
        values.append(value.get("result") if isinstance(value, dict) else value)
    discrimination = checks.get("discrimination")
    if any(value == "fail" for value in values) or discrimination == "fail":
        return "rejected"
    if not all(value == "pass" for value in values):
        return None
    return (
        "calibration-required"
        if discrimination == "calibration-required"
        else None
    )


def _task_qualification_semantics(root: Path, qualification: JSONObject, relative: Path) -> Iterator[Diagnostic]:
    expected = _qualification_decision(qualification)
    if expected is not None and qualification.get("decision") != expected:
        yield Diagnostic(relative.as_posix(), "$.decision", f"must be {expected!r} for the recorded checks")
    reference = _reference_object(root, qualification.get("source_task"), owner=relative, json_path="$.source_task")
    if isinstance(reference, Diagnostic):
        yield reference
        return
    task_path, task = reference
    task_relative = task_path.relative_to(root)
    task_schema_name = _schema_name_for_artifact(
        "diagnostic-task",
        task,
    )
    task_schema = _load_object(
        root,
        _SCHEMA_DIRECTORY / f"{task_schema_name}.schema.json",
    )
    yield from _instance_diagnostics(task, task_schema, task_relative)
    yield from _diagnostic_task_semantics(root, task, task_relative)
    objective = task.get("objective")
    observation = objective.get("observation") if isinstance(objective, dict) else None
    artifact_kind = observation.get("artifact_kind") if isinstance(observation, dict) else None
    if artifact_kind == "executable":
        checks = qualification.get("checks")
        obs_auth = checks.get("observation_authority") if isinstance(checks, dict) else None
        if obs_auth != "fail":
            yield Diagnostic(
                relative.as_posix(),
                "$.checks.observation_authority",
                "executable artifact tasks must record observation_authority as 'fail' until source-separated observer evidence is supported",
            )
        if qualification.get("decision") != "rejected":
            yield Diagnostic(
                relative.as_posix(),
                "$.decision",
                "executable artifact tasks must be rejected until source-separated observer evidence is supported",
            )
    for name in ("task_id", "task_version", "content_digest_sha256"):
        if qualification.get(name) != task.get(name):
            yield Diagnostic(relative.as_posix(), f"$.{name}", "must match the source diagnostic task")
    observed = qualification.get("observed_mapping")
    if isinstance(observed, dict):
        expected_task_digest = canonical_sha256(task)
        if observed.get("task_digest_sha256") != expected_task_digest:
            yield Diagnostic(
                relative.as_posix(),
                "$.observed_mapping.task_digest_sha256",
                "must recapture the canonical source task digest",
            )
        assets = task.get("assets")
        public = assets.get("public") if isinstance(assets, dict) else None
        private = assets.get("verifier_private") if isinstance(assets, dict) else None
        agent = task.get("agent")
        runner = task.get("runner")
        verifier = task.get("verifier")
        expected_fields = {
            "public_tree_digest_sha256": public.get("digest_sha256") if isinstance(public, dict) else None,
            "verifier_private_tree_digest_sha256": private.get("digest_sha256") if isinstance(private, dict) else None,
            "agent_config_digest_sha256": agent.get("config_digest_sha256") if isinstance(agent, dict) else None,
            "runner_config_digest_sha256": runner.get("config_digest_sha256") if isinstance(runner, dict) else None,
            "verifier_config_digest_sha256": verifier.get("config_digest_sha256") if isinstance(verifier, dict) else None,
        }
        for name, value in expected_fields.items():
            if observed.get(name) != value:
                yield Diagnostic(relative.as_posix(), f"$.observed_mapping.{name}", "must match the source task execution mapping")
    version_v2 = (
        qualification.get("schema_version")
        == "omp.task-qualification/v2"
    )
    provenance_name = (
        "evaluation_provenance"
        if version_v2
        else "verifier_provenance"
    )
    provenance = qualification.get(provenance_name)
    runner = task.get("runner")
    verifier = task.get("verifier")
    if isinstance(provenance, dict):
        if version_v2 and isinstance(runner, dict):
            if provenance.get("runner_image") != runner.get("image"):
                yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.runner_image", "must match the source task runner")
            if provenance.get("runner_config_digest_sha256") != runner.get("config_digest_sha256"):
                yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.runner_config_digest_sha256", "must match the source task runner")
            if provenance.get("runner_platform") != runner.get("platform"):
                yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.runner_platform", "must match the source task runner")
        if isinstance(verifier, dict):
            if provenance.get("verifier_image") != verifier.get("image"):
                yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.verifier_image", "must match the source task verifier")
            if provenance.get("verifier_config_digest_sha256") != verifier.get("config_digest_sha256"):
                yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.verifier_config_digest_sha256", "must match the source task verifier")
            if provenance.get("verifier_platform") != verifier.get("platform"):
                yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.verifier_platform", "must match the source task verifier")
        split = task.get("split")
        if isinstance(split, dict) and provenance.get("reviewer_id") == split.get("author_id"):
            yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.reviewer_id", "qualification reviewer must be independent from the task author")
        task_reviews = task.get("reviews")
        if isinstance(task_reviews, dict):
            verifier_review = task_reviews.get("verifier")
            if isinstance(verifier_review, dict):
                if provenance.get("reviewer_id") != verifier_review.get("reviewer"):
                    yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.reviewer_id", "must recapture the verifier reviewer")
                if provenance.get("review_digest_sha256") != verifier_review.get("evidence_digest_sha256"):
                    yield Diagnostic(relative.as_posix(), f"$.{provenance_name}.review_digest_sha256", "must recapture the verifier review evidence digest")
    task_reviews = task.get("reviews")
    qualification_reviews = qualification.get("reviews")
    if qualification_reviews != task_reviews:
        yield Diagnostic(relative.as_posix(), "$.reviews", "must exactly recapture all source task human review attestations")
    license_value = task.get("license")
    if qualification.get("decision") != "rejected" and isinstance(license_value, dict):
        if license_value.get("expression") == "NOASSERTION" or license_value.get("redistribution") != "permitted":
            yield Diagnostic(relative.as_posix(), "$.decision", "cannot admit or calibrate a task without approved redistribution")
    checks = qualification.get("checks")
    if isinstance(checks, dict):
        for name in (
            "baseline_fails",
            "reference_passes",
            "tamper_resistance",
        ):
            check = checks.get(name)
            evidence = (
                check.get("evidence")
                if isinstance(check, dict)
                else None
            )
            if not isinstance(evidence, list):
                continue
            for index, reference in enumerate(evidence):
                if not isinstance(reference, dict):
                    continue
                candidate = _repository_path(
                    root,
                    reference.get("path"),
                )
                json_path = f"$.checks.{name}.evidence[{index}]"
                if candidate is None:
                    yield Diagnostic(
                        relative.as_posix(),
                        f"{json_path}.path",
                        "must name a no-follow repository evidence file",
                    )
                    continue
                try:
                    actual = file_sha256(candidate)
                except ContractError as error:
                    yield Diagnostic(
                        relative.as_posix(),
                        json_path,
                        error.message,
                    )
                else:
                    if reference.get("digest_sha256") != actual:
                        yield Diagnostic(
                            relative.as_posix(),
                            f"{json_path}.digest_sha256",
                            f"must equal evidence file SHA-256 {actual}",
                        )


def _task_pack_semantics(root: Path, pack: JSONObject, relative: Path) -> Iterator[Diagnostic]:
    role = pack.get("role")
    if isinstance(role, str) and role in BUILTIN_ROLES:
        contract_relative = _ROLE_DIRECTORY / f"{role}.json"
        try:
            contract = _load_object(root, contract_relative)
        except ContractError as error:
            yield Diagnostic(error.file or contract_relative.as_posix(), error.json_path, error.message)
        else:
            binding = pack.get("role_contract")
            if isinstance(binding, dict):
                if binding.get("contract_id") != contract.get("contract_id"):
                    yield Diagnostic(relative.as_posix(), "$.role_contract.contract_id", "must match the canonical role contract")
                actual = canonical_sha256(contract)
                if binding.get("digest_sha256") != actual:
                    yield Diagnostic(relative.as_posix(), "$.role_contract.digest_sha256", f"must equal canonical role-contract SHA-256 {actual}")
            if pack.get("task_mix") != contract.get("task_mix"):
                yield Diagnostic(relative.as_posix(), "$.task_mix", "must match the canonical role contract")
            if pack.get("required_capabilities") != contract.get("required_capabilities"):
                yield Diagnostic(relative.as_posix(), "$.required_capabilities", "must exactly match canonical role-required capabilities")
    try:
        is_public = not relative.is_absolute() and relative.is_relative_to(_TASK_PACK_DIRECTORY)
    except AttributeError:
        is_public = False
    if is_public and pack.get("partition") == "holdout":
        yield Diagnostic(relative.as_posix(), "$.partition", "public repository task packs must not be holdout manifests")
    status = pack.get("status")
    if pack.get("routing_eligible") is True and status != "frozen":
        yield Diagnostic(relative.as_posix(), "$.routing_eligible", "only frozen packs may be routing eligible")
    entries = pack.get("entries")
    covered: set[str] = set()
    seen_tasks: set[tuple[JSONValue, JSONValue]] = set()
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            task_ref = _reference_object(root, entry.get("task"), owner=relative, json_path=f"$.entries[{index}].task")
            qualification_ref = _reference_object(root, entry.get("qualification"), owner=relative, json_path=f"$.entries[{index}].qualification")
            if isinstance(task_ref, Diagnostic):
                yield task_ref
                continue
            if isinstance(qualification_ref, Diagnostic):
                yield qualification_ref
                continue
            task_path, task = task_ref
            qualification_path, qualification = qualification_ref
            task_schema_name = _schema_name_for_artifact(
                "diagnostic-task",
                task,
            )
            qualification_schema_name = _schema_name_for_artifact(
                "task-qualification",
                qualification,
            )
            task_schema = _load_object(
                root,
                _SCHEMA_DIRECTORY / f"{task_schema_name}.schema.json",
            )
            qualification_schema = _load_object(
                root,
                _SCHEMA_DIRECTORY / f"{qualification_schema_name}.schema.json",
            )
            yield from _instance_diagnostics(task, task_schema, task_path.relative_to(root))
            yield from _diagnostic_task_semantics(root, task, task_path.relative_to(root))
            source = task.get("source")
            if (
                isinstance(source, dict)
                and source.get("kind") == "synthetic-fixture"
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.entries[{index}].task",
                    "synthetic fixtures cannot enter task packs",
                )
            objective = task.get("objective")
            observation = objective.get("observation") if isinstance(objective, dict) else None
            if (
                isinstance(observation, dict)
                and observation.get("artifact_kind") == "executable"
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.entries[{index}].task",
                    "task packs must reject executable artifact tasks until source-separated observer evidence is supported",
                )
            yield from _instance_diagnostics(qualification, qualification_schema, qualification_path.relative_to(root))
            yield from _task_qualification_semantics(root, qualification, qualification_path.relative_to(root))
            identity = (task.get("task_id"), task.get("task_version"))
            if identity in seen_tasks:
                yield Diagnostic(relative.as_posix(), f"$.entries[{index}].task", "duplicates an earlier task identity")
            seen_tasks.add(identity)
            for name in ("task_id", "task_version", "content_digest_sha256"):
                if qualification.get(name) != task.get(name):
                    yield Diagnostic(relative.as_posix(), f"$.entries[{index}].qualification", f"qualification {name} must match its pack task")
            qualification_source = qualification.get("source_task")
            task_reference = entry.get("task")
            if isinstance(qualification_source, dict) and isinstance(task_reference, dict):
                if qualification_source.get("path") != task_reference.get("path"):
                    yield Diagnostic(relative.as_posix(), f"$.entries[{index}].qualification", "qualification source path must match its pack task reference")
                if qualification_source.get("digest_sha256") != task_reference.get("digest_sha256"):
                    yield Diagnostic(relative.as_posix(), f"$.entries[{index}].qualification", "qualification source digest must match its pack task reference")
            if task.get("role") != pack.get("role") or task.get("task_mix") != pack.get("task_mix"):
                yield Diagnostic(relative.as_posix(), f"$.entries[{index}].task", "task role and task_mix must match the pack")
            if task.get("partition") != pack.get("partition"):
                yield Diagnostic(relative.as_posix(), f"$.entries[{index}].task", "task partition must match the pack")
            task_binding = task.get("role_contract")
            if task_binding != pack.get("role_contract"):
                yield Diagnostic(relative.as_posix(), f"$.entries[{index}].task", "task role-contract binding must match the pack")
            tags = task.get("capability_tags")
            if isinstance(tags, list):
                covered.update(value for value in tags if isinstance(value, str))
            decision = qualification.get("decision")
            if status == "frozen":
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.entries[{index}].qualification",
                    "v1 qualifications cannot freeze a task pack",
                )
            if (
                status == "calibration"
                and decision != "calibration-required"
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.entries[{index}].qualification",
                    "calibration packs require calibration-required qualifications",
                )
            split_review = pack.get("split_review")
            split = task.get("split")
            if status == "frozen" and isinstance(split_review, dict) and isinstance(split, dict):
                if split_review.get("reviewer_id") == split.get("author_id"):
                    yield Diagnostic(relative.as_posix(), "$.split_review.reviewer_id", "frozen split review must be independent from task authors")
    required = pack.get("required_capabilities")
    if isinstance(required, list) and entries and (missing := sorted(set(required) - covered)):
        yield Diagnostic(relative.as_posix(), "$.required_capabilities", f"entries do not cover required capabilities {missing!r}")


def _schema_name_for_artifact(
    schema_name: str,
    artifact: JSONObject,
) -> str:
    versions = _VERSIONED_ARTIFACT_SCHEMAS.get(schema_name)
    if versions is None:
        return schema_name
    version = artifact.get("schema_version")
    if isinstance(version, str):
        return versions.get(version, schema_name)
    return schema_name


def _ledger_entry_semantics(root: Path, entry: JSONObject, relative: Path) -> Iterator[Diagnostic]:
    payload = entry.get("payload")
    schema_name = entry.get("payload_schema")
    if isinstance(payload, dict) and isinstance(schema_name, str):
        actual = canonical_sha256(payload)
        if entry.get("payload_digest_sha256") != actual:
            yield Diagnostic(relative.as_posix(), "$.payload_digest_sha256", f"must equal canonical payload SHA-256 {actual}")
        effective_schema_name = _schema_name_for_artifact(
            schema_name,
            payload,
        )
        schema_relative = (
            _SCHEMA_DIRECTORY / f"{effective_schema_name}.schema.json"
        )
        try:
            schema = _load_object(root, schema_relative)
        except ContractError as error:
            yield Diagnostic(error.file or schema_relative.as_posix(), error.json_path, error.message)
        else:
            yield from _instance_diagnostics(payload, schema, relative)
            yield from _artifact_semantics(root, schema_name, payload, relative)


def _artifact_semantics(
    root: Path,
    schema_name: str,
    artifact: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    if schema_name == _WORKER_RUN_MANIFEST_SCHEMA:
        yield from _worker_run_manifest_semantics(root, artifact, relative)
    elif schema_name == "attempt-observation":
        yield from _attempt_observation_semantics(artifact, relative)
    elif schema_name == "attempt-outcome":
        yield from _attempt_outcome_semantics(artifact, relative)
    elif schema_name == "route-policy":
        yield from _route_policy_semantics(artifact, relative)
    elif schema_name == "routing-decision":
        yield from _routing_decision_semantics(artifact, relative)
    elif schema_name == "scored-worker-policy":
        yield from _scored_worker_policy_semantics(artifact, relative)
    elif schema_name == "task-candidate":
        yield from _task_candidate_semantics(root, artifact, relative)
    elif schema_name == "diagnostic-task":
        yield from _diagnostic_task_semantics(root, artifact, relative)
    elif schema_name == "task-qualification":
        yield from _task_qualification_semantics(root, artifact, relative)
    elif schema_name == "task-pack":
        yield from _task_pack_semantics(root, artifact, relative)
    elif schema_name == "experiment-ledger-entry":
        yield from _ledger_entry_semantics(root, artifact, relative)


def _safe_schema(root: Path, relative: Path, diagnostics: list[Diagnostic]) -> JSONObject | None:
    try:
        return _load_object(root, relative)
    except ContractError as error:
        diagnostics.append(Diagnostic(error.file or relative.as_posix(), error.json_path, error.message))
        return None

def _schema_names(root: Path) -> tuple[str, ...]:
    suffix = ".schema.json"
    return tuple(
        path.name.removesuffix(suffix)
        for path in sorted((root / _SCHEMA_DIRECTORY).glob(f"*{suffix}"))
    )


def _load_artifact(path: Path, display_path: Path) -> JSONObject:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractError(str(error), display_path.as_posix()) from error
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
            display_path.as_posix(),
        ) from error
    value = _as_json(decoded, file=display_path.as_posix())
    if not isinstance(value, dict):
        raise ContractError("document must be a JSON object", display_path.as_posix())
    return value


def validate_artifact(
    root: Path | None,
    schema_name: str,
    artifact_path: Path,
) -> ValidationResult:
    """Validate one JSON artifact against a named repository schema."""

    resolved = resolve_root(root)
    names = _schema_names(resolved)
    if schema_name not in names:
        raise ContractError(
            f"unknown schema {schema_name!r}; expected one of {list(names)!r}"
        )

    path = artifact_path.expanduser()
    if not path.is_absolute():
        path = resolved / path
    path = path.resolve()
    try:
        display_path = path.relative_to(resolved)
    except ValueError:
        display_path = path

    diagnostics: list[Diagnostic] = []
    try:
        artifact = _load_artifact(path, display_path)
    except ContractError as error:
        diagnostics.append(
            Diagnostic(
                error.file or display_path.as_posix(),
                error.json_path,
                error.message,
            )
        )
        return ValidationResult(tuple(sorted(set(diagnostics))))

    effective_schema_name = _schema_name_for_artifact(
        schema_name,
        artifact,
    )
    schema_relative = (
        _SCHEMA_DIRECTORY / f"{effective_schema_name}.schema.json"
    )
    schema = _safe_schema(resolved, schema_relative, diagnostics)
    if schema is None:
        return ValidationResult(tuple(sorted(set(diagnostics))))
    diagnostics.extend(_schema_diagnostics(schema, schema_relative))
    if diagnostics:
        return ValidationResult(tuple(sorted(set(diagnostics))))

    diagnostics.extend(
        _instance_diagnostics(artifact, schema, display_path)
    )
    diagnostics.extend(
        _artifact_semantics(
            resolved,
            schema_name,
            artifact,
            display_path,
        )
    )
    return ValidationResult(tuple(sorted(set(diagnostics))))

def validate_value(
    root: Path | None,
    schema_name: str,
    artifact: JSONObject,
    display_path: Path,
) -> ValidationResult:
    """Validate one already-captured JSON object without reopening its source path."""

    resolved = resolve_root(root)
    names = _schema_names(resolved)
    if schema_name not in names:
        raise ContractError(
            f"unknown schema {schema_name!r}; expected one of {list(names)!r}"
        )
    effective_schema_name = _schema_name_for_artifact(
        schema_name,
        artifact,
    )
    schema_relative = (
        _SCHEMA_DIRECTORY / f"{effective_schema_name}.schema.json"
    )
    diagnostics: list[Diagnostic] = []
    schema = _safe_schema(resolved, schema_relative, diagnostics)
    if schema is not None:
        diagnostics.extend(_schema_diagnostics(schema, schema_relative))
        diagnostics.extend(
            _instance_diagnostics(artifact, schema, display_path)
        )
        diagnostics.extend(
            _artifact_semantics(
                resolved,
                schema_name,
                artifact,
                display_path,
            )
        )
    return ValidationResult(tuple(sorted(set(diagnostics))))


def validate_repository(root: Path | None = None) -> ValidationResult:
    """Validate schemas, instances, and cross-document semantic invariants."""

    resolved = resolve_root(root)
    diagnostics: list[Diagnostic] = []

    schema_files = sorted((resolved / _SCHEMA_DIRECTORY).glob("*.json"))
    schemas: dict[str, JSONObject] = {}
    for schema_path in schema_files:
        relative = schema_path.relative_to(resolved)
        schema = _safe_schema(resolved, relative, diagnostics)
        if schema is not None:
            schema_errors = _schema_diagnostics(schema, relative)
            diagnostics.extend(schema_errors)
            if not schema_errors:
                schemas[schema_path.name] = schema

    for required_schema in (
        _REGISTRY_SCHEMA,
        _ROLE_SCHEMA,
        _SCORED_WORKER_POLICY_SCHEMA,
        "task-candidate.schema.json",
        _DIAGNOSTIC_TASK_SCHEMA,
        _TASK_QUALIFICATION_SCHEMA,
        _TASK_REVIEW_EVIDENCE_SCHEMA,
        _TASK_PACK_SCHEMA,
        _EXPERIMENT_LEDGER_ENTRY_SCHEMA,
    ):
        if required_schema not in schemas:
            diagnostics.append(
                Diagnostic(
                    (_SCHEMA_DIRECTORY / required_schema).as_posix(),
                    "$",
                    "required schema file is missing or invalid",
                )
            )

    registry = _safe_schema(resolved, _REGISTRY_FILE, diagnostics)
    if registry is None:
        return ValidationResult(tuple(sorted(set(diagnostics))))

    registry_schema = schemas.get(_REGISTRY_SCHEMA)
    if registry_schema is not None:
        diagnostics.extend(_instance_diagnostics(registry, registry_schema, _REGISTRY_FILE))
    diagnostics.extend(_registry_semantics(registry))

    contracts = registry.get("contracts")
    expected_files = {f"{role}.json" for role in BUILTIN_ROLES}
    role_directory = resolved / _ROLE_DIRECTORY
    actual_files = (
        {path.relative_to(role_directory).as_posix() for path in role_directory.rglob("*.json")}
        if role_directory.is_dir()
        else set()
    )
    for name in sorted(expected_files - actual_files):
        diagnostics.append(Diagnostic((_ROLE_DIRECTORY / name).as_posix(), "$", "required manifest file is missing"))
    for name in sorted(actual_files - expected_files):
        diagnostics.append(Diagnostic((_ROLE_DIRECTORY / name).as_posix(), "$", "unexpected manifest file"))

    role_schema = schemas.get(_ROLE_SCHEMA)
    contract_ids: dict[str, str] = {}
    for role in BUILTIN_ROLES:
        relative = _ROLE_DIRECTORY / f"{role}.json"
        if not (resolved / relative).is_file():
            continue
        manifest = _safe_schema(resolved, relative, diagnostics)
        if manifest is None:
            continue
        if role_schema is not None:
            diagnostics.extend(_instance_diagnostics(manifest, role_schema, relative))
        diagnostics.extend(_manifest_semantics(role, manifest, relative))
        contract_id = manifest.get("contract_id")
        if isinstance(contract_id, str):
            previous = contract_ids.get(contract_id)
            if previous is not None:
                diagnostics.append(
                    Diagnostic(
                        relative.as_posix(),
                        "$.contract_id",
                        f"contract_id duplicates {previous}",
                    )
                )
            else:
                contract_ids[contract_id] = relative.as_posix()
        if isinstance(contracts, dict):
            mapped = contracts.get(role)
            if isinstance(mapped, str) and mapped != relative.as_posix():
                diagnostics.append(
                    Diagnostic(relative.as_posix(), "$", f"registry maps role {role!r} to {mapped!r}")
                )

    task_pack_map = registry.get("task_packs")
    expected_pack_files = {f"{role}-v1.json" for role in _PILOT_TASK_PACKS}
    pack_directory = resolved / _TASK_PACK_DIRECTORY
    actual_pack_files = (
        {path.relative_to(pack_directory).as_posix() for path in pack_directory.rglob("*.json")}
        if pack_directory.is_dir()
        else set()
    )
    for name in sorted(expected_pack_files - actual_pack_files):
        diagnostics.append(Diagnostic((_TASK_PACK_DIRECTORY / name).as_posix(), "$", "required task pack is missing"))
    for name in sorted(actual_pack_files - expected_pack_files):
        diagnostics.append(Diagnostic((_TASK_PACK_DIRECTORY / name).as_posix(), "$", "unexpected public task pack"))
    task_pack_schema = schemas.get(_TASK_PACK_SCHEMA)
    for role in _PILOT_TASK_PACKS:
        relative = _TASK_PACK_DIRECTORY / f"{role}-v1.json"
        if not (resolved / relative).is_file():
            continue
        pack = _safe_schema(resolved, relative, diagnostics)
        if pack is None:
            continue
        if task_pack_schema is not None:
            diagnostics.extend(_instance_diagnostics(pack, task_pack_schema, relative))
        diagnostics.extend(_task_pack_semantics(resolved, pack, relative))
        if isinstance(task_pack_map, dict) and task_pack_map.get(role) != relative.as_posix():
            diagnostics.append(Diagnostic(relative.as_posix(), "$", f"registry must map role {role!r} to this canonical pack"))

    policy = _safe_schema(resolved, _SCORED_WORKER_POLICY_FILE, diagnostics)
    if policy is None:
        diagnostics.append(
            Diagnostic(
                _SCORED_WORKER_POLICY_FILE.as_posix(),
                "$",
                "required canonical policy file is missing or invalid",
            )
        )
    else:
        policy_schema = schemas.get(_SCORED_WORKER_POLICY_SCHEMA)
        if policy_schema is not None:
            diagnostics.extend(
                _instance_diagnostics(
                    policy,
                    policy_schema,
                    _SCORED_WORKER_POLICY_FILE,
                )
            )
        diagnostics.extend(
            _artifact_semantics(
                resolved,
                "scored-worker-policy",
                policy,
                _SCORED_WORKER_POLICY_FILE,
            )
        )

    return ValidationResult(tuple(sorted(set(diagnostics))))
