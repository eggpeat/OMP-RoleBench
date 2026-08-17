"""Fail-closed Docker/runsc execution for provider-disabled scored workers."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import selectors
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import BinaryIO, Callable, Protocol

from .accounting import AccountingError, classify_attempt
from .contracts import (
    JSONObject,
    JSONValue,
    canonical_json,
    validate_artifact,
    validate_value,
)
from .image_identity import (
    ImageIdentityError,
    image_config_digest_from_archive,
)
from .runner_protocol import (
    ProtocolError,
    build_runner_evidence,
    compute_evaluation_request_digest,
    parse_verifier_result,
    validate_verifier_result,
)

_TASK_LABEL_PREFIX = "org.omp.rolebench.task."
_TASK_ROLE_LABEL = f"{_TASK_LABEL_PREFIX}role"
_TASK_STAGE_LABEL = f"{_TASK_LABEL_PREFIX}stage"
_PUBLIC_TREE_LABEL = f"{_TASK_LABEL_PREFIX}public-tree-sha256"
_VERIFIER_PRIVATE_TREE_LABEL = (
    f"{_TASK_LABEL_PREFIX}verifier-private-tree-sha256"
)
_SINGLE_PLATFORM_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)

# Explicit host-safety ceiling for fallback Docker image archives.
MAX_IMAGE_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class _TaskBindingError(RuntimeError):
    """An effective task image differs from its prepared binding."""


class WorkerError(ValueError):
    """A worker manifest, policy, or internal accounting record is invalid."""


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class _StreamResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    overflowed: bool = False


class _Adapter(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
        input_source: bytes | None = None,
    ) -> _CommandResult: ...

    def stream(
        self,
        argv: list[str],
        *,
        input_source: bytes | BinaryIO | None,
        timeout: float,
        output_limit: int,
        output_sink: BinaryIO | None = None,
    ) -> _StreamResult: ...


class _SubprocessAdapter:
    """Small argv-only subprocess boundary, replaceable by deterministic tests."""

    def run(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
        input_source: bytes | None = None,
    ) -> _CommandResult:
        completed = subprocess.run(
            argv,
            input=input_source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
        )
        return _CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def stream(
        self,
        argv: list[str],
        *,
        input_source: bytes | BinaryIO | None,
        timeout: float,
        output_limit: int,
        output_sink: BinaryIO | None = None,
    ) -> _StreamResult:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input_source is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
        )
        assert process.stdout is not None and process.stderr is not None
        if input_source is not None:

            def feed_stdin() -> None:
                assert process.stdin is not None
                try:
                    if isinstance(input_source, bytes):
                        process.stdin.write(input_source)
                    else:
                        input_source.seek(0)
                        while chunk := input_source.read(64 * 1024):
                            process.stdin.write(chunk)
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    process.stdin.close()

            threading.Thread(target=feed_stdin, daemon=True).start()

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        out = bytearray()
        err = bytearray()
        total_output_size = 0
        deadline = time.monotonic() + timeout
        timed_out = False
        overflowed = False
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    process.kill()
                    break
                events = selector.select(min(remaining, 0.1))
                if not events and process.poll() is not None:
                    continue
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if total_output_size + len(chunk) > output_limit:
                        keep = max(0, output_limit - total_output_size)
                        if key.data == "stdout":
                            if output_sink is None:
                                out.extend(chunk[:keep])
                            else:
                                output_sink.write(chunk[:keep])
                        else:
                            err.extend(chunk[:keep])
                        total_output_size += keep
                        overflowed = True
                        process.kill()
                        break
                    if key.data == "stdout":
                        if output_sink is None:
                            out.extend(chunk)
                        else:
                            output_sink.write(chunk)
                    else:
                        err.extend(chunk)
                    total_output_size += len(chunk)
                if timed_out or overflowed:
                    break
        except BaseException:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            selector.close()
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait()
        return _StreamResult(returncode, bytes(out), bytes(err), timed_out, overflowed)

_ADAPTER_FACTORY: Callable[[], _Adapter] = _SubprocessAdapter


def capture_command(
    argv: list[str],
    *,
    timeout: float,
    output_limit: int,
    output_sink: BinaryIO | None = None,
) -> _StreamResult:
    """Run one argv-only command with bounded output."""
    return _SubprocessAdapter().stream(
        argv,
        input_source=None,
        timeout=timeout,
        output_limit=output_limit,
        output_sink=output_sink,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_stream(source: BinaryIO) -> str:
    source.seek(0)
    digest = hashlib.sha256()
    while chunk := source.read(64 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _diagnostics(result: object) -> str:
    diagnostics = getattr(result, "diagnostics", ())
    return "; ".join(
        f"{item.file}:{item.json_path}: {item.message}" for item in diagnostics
    )


def _safe_relative(root: Path, raw: str, *, label: str) -> Path:
    root = root.resolve()
    candidate = (
        (root / raw).resolve()
        if not Path(raw).is_absolute()
        else Path(raw).resolve()
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkerError(f"{label} must remain inside the repository") from exc
    return candidate


def _capture_object(
    root: Path,
    path: Path,
    *,
    label: str,
) -> tuple[JSONObject, Path, tuple[int, int, int, int, int], str]:
    selected = _safe_relative(root, str(path), label=f"{label} path")
    relative = selected.relative_to(root)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(selected, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise WorkerError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise WorkerError(f"{label} changed while being read")
        data = b"".join(chunks)
    except OSError as exc:
        raise WorkerError(f"cannot capture {label}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkerError(f"cannot load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerError(f"{label} must be a JSON object")
    return value, relative, identity, canonical_json(value)


def _verify_capture_stable(
    root: Path,
    relative: Path,
    identity: tuple[int, int, int, int, int],
    *,
    label: str,
) -> None:
    selected = _safe_relative(root, str(relative), label=f"{label} path")
    try:
        current = os.lstat(selected)
    except OSError as exc:
        raise WorkerError(f"cannot verify {label}: {exc}") from exc
    if not stat.S_ISREG(current.st_mode) or (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
    ) != identity:
        raise WorkerError(f"{label} changed after capture")


def _local_socket() -> Path:
    socket = Path(f"/run/user/{os.getuid()}/docker.sock")
    try:
        metadata = socket.stat()
    except OSError as exc:
        raise WorkerError(f"rootless Docker socket is unavailable: {exc}") from exc
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise WorkerError("rootless Docker socket must be a user-owned Unix socket")
    return socket


def local_docker_argv(docker: str, *arguments: str) -> list[str]:
    return [docker, "--host", f"unix://{_local_socket()}", *arguments]


def _validated_policy(
    root: Path,
    path: Path,
) -> tuple[
    JSONObject,
    str,
    tuple[Path, tuple[int, int, int, int, int]],
]:
    policy, relative, identity, canonical = _capture_object(
        root,
        path,
        label="scored-worker policy",
    )
    validation = validate_value(root, "scored-worker-policy", policy, relative)
    if not validation.valid:
        raise WorkerError(f"invalid scored-worker policy: {_diagnostics(validation)}")
    if policy.get("schema_version") != "omp.scored-worker-policy/v2":
        raise WorkerError(
            "current worker requires omp.scored-worker-policy/v2"
        )
    return policy, _sha256(canonical.encode("utf-8")), (relative, identity)


def _docker_info(
    adapter: _Adapter, docker: str
) -> tuple[_CommandResult | None, JSONObject | None, str | None]:
    try:
        result = adapter.run(
            local_docker_argv(docker, "info", "--format", "{{json .}}"),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, WorkerError) as exc:
        return None, None, type(exc).__name__
    if result.returncode != 0:
        return result, None, "docker-info-failed"
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError):
        return result, None, "docker-info-malformed"
    if not isinstance(value, dict):
        return result, None, "docker-info-malformed"
    return result, value, None


def _resource_wrapper_probe(
    adapter: _Adapter,
    info: JSONObject | None,
) -> tuple[bool, str | None]:
    runtimes = info.get("Runtimes") if info is not None else None
    runsc = runtimes.get("runsc") if isinstance(runtimes, dict) else None
    path = runsc.get("path") if isinstance(runsc, dict) else None
    if not isinstance(path, str) or not path:
        return False, "runsc-resource-wrapper-missing"
    try:
        result = adapter.run([path, "--rolebench-doctor"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False, "runsc-resource-wrapper-unavailable"
    if result.returncode != 0:
        return False, "runsc-resource-wrapper-unready"
    try:
        report = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError):
        return False, "runsc-resource-wrapper-malformed"
    controllers = report.get("controllers") if isinstance(report, dict) else None
    if (
        not isinstance(report, dict)
        or report.get("schema_version") != "omp.runsc-wrapper-doctor/v1"
        or report.get("ready") is not True
        or not isinstance(controllers, list)
        or set(controllers) != {"cpu", "memory", "pids"}
    ):
        return False, "runsc-resource-wrapper-unready"
    return True, None


def doctor_worker(
    root: Path, policy_path: Path, *, docker: str = "docker"
) -> JSONObject:
    """Report whether the exact local rootless Docker/runsc prerequisites are ready."""
    root = Path(root).resolve()
    diagnostics: list[JSONValue] = []
    try:
        _local_socket()
        socket_valid = True
    except WorkerError as exc:
        diagnostics.append(f"docker-socket-invalid:{type(exc).__name__}")
        socket_valid = False
    try:
        selected = _safe_relative(root, str(policy_path), label="policy path")
        _validated_policy(root, selected)
        policy_valid = True
    except (WorkerError, OSError) as exc:
        diagnostics.append(f"policy-invalid:{type(exc).__name__}")
        policy_valid = False

    adapter = _ADAPTER_FACTORY()
    result, info, error = _docker_info(adapter, docker)
    executable = result is not None
    server = info is not None
    if error is not None:
        diagnostics.append(error)
    security_options = info.get("SecurityOptions", []) if info else []
    rootless = False
    if isinstance(security_options, list):
        for option in security_options:
            if isinstance(option, str):
                rootless = rootless or option.strip().lower() == "name=rootless"
            elif isinstance(option, dict):
                normalized = {
                    str(key).strip().lower(): str(value).strip().lower()
                    for key, value in option.items()
                }
                rootless = rootless or normalized == {"name": "rootless"}
    runtimes = info.get("Runtimes", {}) if info else {}
    runsc = isinstance(runtimes, dict) and "runsc" in runtimes
    cgroup_v2 = bool(info) and str(info.get("CgroupVersion")) == "2"
    delegation = cgroup_v2 and bool(info) and info.get("CgroupDriver") == "systemd"
    resource_enforcement, wrapper_error = _resource_wrapper_probe(adapter, info)
    if wrapper_error is not None:
        diagnostics.append(wrapper_error)
    checks = {
        "docker-executable": executable,
        "docker-server": server,
        "rootless": rootless,
        "runsc": runsc,
        "cgroup-v2": cgroup_v2,
        "delegation": delegation,
        "resource-enforcement": resource_enforcement,
        "policy": policy_valid,
        "local-socket": socket_valid,
    }
    diagnostics.extend(
        f"requirement-failed:{name}"
        for name, passed in checks.items()
        if not passed
    )
    ready = all(checks.values())
    return {
        "schema_version": "omp.worker-doctor-report/v1",
        "ready": ready,
        "policy_valid": policy_valid,
        "docker_executable": executable,
        "docker_server": server,
        "rootless": rootless,
        "runsc": runsc,
        "cgroup_v2": cgroup_v2,
        "delegation": delegation,
        "local_socket": socket_valid,
        "resource_enforcement": resource_enforcement,
        "diagnostics": diagnostics,
    }


def _image_digest(image: str) -> str:
    marker = "@sha256:"
    if marker not in image:
        raise WorkerError("container image is not digest-pinned")
    digest = image.rsplit(marker, 1)[1]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise WorkerError("container image has an invalid sha256 digest")
    return digest


def _require_object(value: JSONValue | None, label: str) -> JSONObject:
    if not isinstance(value, dict):
        raise WorkerError(f"{label} must be an object")
    return value


def _manifest_task_binding(manifest: JSONObject) -> JSONObject | None:
    task = _require_object(manifest.get("task"), "task")
    tree_fields = (
        "public_tree_digest_sha256",
        "verifier_private_tree_digest_sha256",
    )
    task_present = [
        field in task
        for field in (
            *tree_fields,
            "qualification_digest_sha256",
            "evidence_use",
        )
    ]
    if not any(task_present):
        return None
    if (
        not all(field in task for field in tree_fields)
        or task.get("evidence_use")
        not in {"admission-only", "calibration-only"}
    ):
        raise WorkerError("task image binding is incomplete")
    evidence_use = task["evidence_use"]
    qualification_present = "qualification_digest_sha256" in task
    if (evidence_use == "admission-only" and qualification_present) or (
        evidence_use == "calibration-only" and not qualification_present
    ):
        raise WorkerError(
            "task qualification binding conflicts with evidence_use"
        )
    digests = [
        task["digest_sha256"],
        *(task[field] for field in tree_fields),
    ]
    qualification_digest = task.get("qualification_digest_sha256")
    if isinstance(qualification_digest, str):
        digests.append(qualification_digest)
    if any(digest in {"0" * 64, "f" * 64} for digest in digests):
        raise WorkerError("task image binding contains a placeholder digest")
    if (
        task["public_tree_digest_sha256"]
        == task["verifier_private_tree_digest_sha256"]
    ):
        raise WorkerError("public and verifier-private task trees must differ")
    return {
        "task": task,
    }


def _expected_task_labels(
    manifest: JSONObject,
    *,
    stage: str,
) -> dict[str, str]:
    task = _require_object(manifest.get("task"), "task")
    labels = {
        _TASK_ROLE_LABEL: str(manifest["role"]),
        _TASK_STAGE_LABEL: stage,
    }
    if stage == "verifier":
        labels[_VERIFIER_PRIVATE_TREE_LABEL] = str(
            task["verifier_private_tree_digest_sha256"]
        )
    else:
        labels[_PUBLIC_TREE_LABEL] = str(task["public_tree_digest_sha256"])
    return labels


def _inspect_image(
    adapter: _Adapter,
    docker: str,
    image: str,
    *,
    timeout: float,
) -> JSONObject:
    started = time.monotonic()
    result = _run(
        adapter,
        local_docker_argv(
            docker,
            "image",
            "inspect",
            image,
            "--format",
            "{{json .}}",
        ),
        timeout=timeout,
    )
    if result.returncode != 0:
        raise _TaskBindingError("image-inspect-failed")
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _TaskBindingError("image-inspect-malformed") from exc
    if not isinstance(value, dict):
        raise _TaskBindingError("image-inspect-malformed")

    engine_id = value.get("Id")
    descriptor = value.get("Descriptor")
    manifest_digest = (
        descriptor.get("digest")
        if isinstance(descriptor, dict)
        else None
    )
    engine_digest = (
        engine_id.removeprefix("sha256:")
        if isinstance(engine_id, str)
        else None
    )
    if (
        isinstance(manifest_digest, str)
        and manifest_digest.startswith("sha256:")
        and isinstance(engine_digest, str)
        and len(engine_digest) == 64
        and all(character in "0123456789abcdef" for character in engine_digest)
        and engine_id != manifest_digest
    ):
        config_digest = engine_digest
    else:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired("image-identity", timeout)
        with tempfile.TemporaryDirectory(
            prefix="rolebench-image-identity-"
        ) as temporary:
            archive_path = Path(temporary) / "image.tar"
            try:
                with archive_path.open("w+b") as archive:
                    exported = adapter.stream(
                        local_docker_argv(
                            docker,
                            "image",
                            "save",
                            image,
                        ),
                        input_source=None,
                        timeout=remaining,
                        output_limit=MAX_IMAGE_ARCHIVE_BYTES,
                        output_sink=archive,
                    )
                    if exported.timed_out:
                        raise subprocess.TimeoutExpired(
                            "image-identity",
                            remaining,
                        )
                    if exported.overflowed:
                        raise _TaskBindingError(
                            "image-config-export-size-limit-exceeded"
                        )
                    if exported.returncode != 0:
                        raise _TaskBindingError(
                            "image-config-export-failed"
                        )
                    archive.flush()
                config_digest = image_config_digest_from_archive(
                    archive_path,
                    _image_digest(image),
                )
            except OSError as exc:
                raise _TaskBindingError(
                    "image-config-export-failed"
                ) from exc
            except ImageIdentityError as exc:
                raise _TaskBindingError(
                    "image-config-identity-invalid"
                ) from exc
    if (
        not isinstance(engine_id, str)
        or not engine_id.startswith("sha256:")
        or len(engine_id) != 71
        or any(
            character not in "0123456789abcdef"
            for character in engine_id.removeprefix("sha256:")
        )
    ):
        raise _TaskBindingError("image-engine-id-invalid")
    value["_rolebench_config_digest"] = f"sha256:{config_digest}"
    value["_rolebench_engine_image_id"] = engine_id
    return value


def _image_binding_facts(
    inspect: JSONObject,
    manifest: JSONObject,
    container: JSONObject,
    *,
    stage: str,
    task_bound: bool,
) -> JSONObject:
    descriptor = inspect.get("Descriptor")
    config = inspect.get("Config")
    repo_digests = inspect.get("RepoDigests")
    labels = config.get("Labels") if isinstance(config, dict) else None
    platform = _require_object(container.get("platform"), "container platform")
    image = str(container["image"])
    manifest_digest = f"sha256:{_image_digest(image)}"
    config_digest = f"sha256:{container['config_digest_sha256']}"
    facts: JSONObject = {
        "reference_exact": isinstance(repo_digests, list)
        and image in repo_digests,
        "single_platform_manifest": isinstance(descriptor, dict)
        and descriptor.get("mediaType") in _SINGLE_PLATFORM_MANIFEST_MEDIA_TYPES,
        "manifest_digest_exact": isinstance(descriptor, dict)
        and descriptor.get("digest") == manifest_digest,
        "config_digest_exact": inspect.get("_rolebench_config_digest") == config_digest,
        "platform_exact": inspect.get("Os") == platform["os"]
        and inspect.get("Architecture") == platform["architecture"]
        and inspect.get("Variant") == platform["variant"],
    }
    if task_bound:
        expected_labels = _expected_task_labels(manifest, stage=stage)
        observed_task_labels = (
            {
                str(key): str(value)
                for key, value in labels.items()
                if str(key).startswith(_TASK_LABEL_PREFIX)
            }
            if isinstance(labels, dict)
            else {}
        )
        facts["task_labels_exact"] = observed_task_labels == expected_labels
    return facts


def _load_manifest(
    root: Path,
    captured_manifest: tuple[
        JSONObject,
        Path,
        tuple[int, int, int, int, int],
        str,
    ],
) -> tuple[
    JSONObject,
    Path,
    JSONObject,
    str,
    tuple[Path, tuple[int, int, int, int, int]],
    tuple[Path, tuple[int, int, int, int, int]],
]:
    manifest, relative, identity, _ = captured_manifest
    validation = validate_value(root, "worker-run-manifest", manifest, relative)
    if not validation.valid:
        raise WorkerError(f"invalid worker run manifest: {_diagnostics(validation)}")
    provider = _require_object(manifest.get("provider"), "provider")
    if provider != {"enabled": False}:
        raise WorkerError("worker provider must be exactly disabled")
    policy_ref = _require_object(manifest.get("policy"), "policy")
    raw_path = policy_ref.get("path")
    expected_digest = policy_ref.get("digest_sha256")
    if not isinstance(raw_path, str) or not isinstance(expected_digest, str):
        raise WorkerError("manifest policy reference is invalid")
    policy_path = _safe_relative(root, raw_path, label="policy path")
    policy, policy_digest, policy_identity = _validated_policy(root, policy_path)
    if policy_digest != expected_digest:
        raise WorkerError("manifest policy digest does not match canonical policy")
    agent = _require_object(manifest.get("agent"), "agent")
    runner = _require_object(manifest.get("runner"), "runner")
    verifier = _require_object(manifest.get("verifier"), "verifier")
    agent_digest = _image_digest(str(agent.get("image", "")))
    runner_digest = _image_digest(str(runner.get("image", "")))
    verifier_digest = _image_digest(str(verifier.get("image", "")))
    image_digests = [agent_digest, runner_digest, verifier_digest]
    if len(set(image_digests)) != len(image_digests):
        raise WorkerError(
            "agent, runner, and verifier image digests must differ"
        )
    for digest in image_digests:
        if digest in {"0" * 64, "f" * 64}:
            raise WorkerError(
                "placeholder image digests must be replaced before execution"
            )

    for name, container in (
        ("agent", agent),
        ("runner", runner),
        ("verifier", verifier),
    ):
        config_digest = container.get("config_digest_sha256")
        if not isinstance(config_digest, str) or len(config_digest) != 64:
            raise WorkerError(
                f"{name} config_digest_sha256 is missing or invalid"
            )
        if config_digest in {"0" * 64, "f" * 64}:
            raise WorkerError(
                f"{name} config digest cannot be a placeholder"
            )
        _require_object(container.get("platform"), f"{name} platform")

    config_digests = [
        str(agent["config_digest_sha256"]),
        str(runner["config_digest_sha256"]),
        str(verifier["config_digest_sha256"]),
    ]
    if len(set(config_digests)) != len(config_digests):
        raise WorkerError(
            "agent, runner, and verifier image config digests must differ"
        )

    _manifest_task_binding(manifest)
    return (
        manifest,
        policy_path,
        policy,
        policy_digest,
        (relative, identity),
        policy_identity,
    )


def _remaining(deadline: float, cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(remaining, cap)


def _run(
    adapter: _Adapter, argv: list[str], *, timeout: float
) -> _CommandResult:
    if timeout <= 0:
        raise subprocess.TimeoutExpired(argv, timeout)
    return adapter.run(argv, timeout=timeout)


def _create_args(
    docker: str,
    name: str,
    image: str,
    argv: list[str],
    policy: JSONObject,
    *,
    stage: str,
    ownership: str,
) -> list[str]:
    executor = _require_object(policy.get("executor"), "policy.executor")
    resources = _require_object(policy.get("resources"), "policy.resources")
    principal = _require_object(
        _require_object(policy.get("verifier"), "policy.verifier").get("user")
        if stage == "verifier"
        else executor.get("user"),
        "container user",
    )
    uid, gid = principal.get("uid"), principal.get("gid")
    if not isinstance(uid, int) or not isinstance(gid, int):
        raise WorkerError("container uid and gid must be numeric")
    args = local_docker_argv(
        docker,
        "create",
        "--name",
        name,
        "--label",
        f"org.omp.rolebench.owner={ownership}",
        "--runtime",
        "runsc",
        "--user",
        f"{uid}:{gid}",
        "--read-only",
        "--log-driver",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--network",
        "none",
    )
    if stage in {"runner", "verifier"}:
        args.append("--interactive")
    if stage in {"agent", "runner"}:
        scratch = _require_object(executor.get("scratch"), "policy.executor.scratch")
        size = scratch.get("size_bytes")
        if not isinstance(size, int):
            raise WorkerError("scratch size must be an integer")
        args += [
            "--tmpfs",
            f"/workspace:rw,nosuid,nodev,noexec,size={size},uid={uid},gid={gid},mode=0700",
        ]
    cpu = resources.get("cpu_limit")
    memory = resources.get("memory_bytes")
    pids = resources.get("pids_limit")
    nofile = resources.get("open_files_limit")
    args += [
        "--cpus",
        str(cpu),
        "--memory",
        str(memory),
        "--memory-swap",
        str(memory),
        "--pids-limit",
        str(pids),
        "--ulimit",
        f"nofile={nofile}:{nofile}",
        "--",
        image,
        *argv,
    ]
    return args


def _inspect(
    adapter: _Adapter, docker: str, container: str, timeout: float
) -> JSONObject:
    result = _run(
        adapter,
        local_docker_argv(docker, "inspect", container),
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError("container-inspect-failed")
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("container-inspect-malformed") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError("container-inspect-malformed")
    return value[0]


def _isolation_facts(
    inspect: JSONObject,
    policy: JSONObject,
    *,
    stage: str,
    expected_image: str,
    ownership: str,
    expected_engine_image_id: str,
    expected_task_labels: dict[str, str] | None = None,
) -> JSONObject:
    host = inspect.get("HostConfig")
    config = inspect.get("Config")
    mounts = inspect.get("Mounts")
    if not isinstance(host, dict):
        host = {}
    if not isinstance(config, dict):
        config = {}
    executor = _require_object(policy.get("executor"), "policy.executor")
    resources = _require_object(policy.get("resources"), "policy.resources")
    user = _require_object(
        _require_object(policy.get("verifier"), "policy.verifier").get("user")
        if stage == "verifier"
        else executor.get("user"),
        "container user",
    )
    expected_user = f"{user.get('uid')}:{user.get('gid')}"
    cap_drop = host.get("CapDrop")
    cap_add = host.get("CapAdd")
    security = host.get("SecurityOpt")
    labels = config.get("Labels")
    log_config = host.get("LogConfig")
    devices = host.get("Devices")
    tmpfs = host.get("Tmpfs")
    ulimits = host.get("Ulimits")
    nofile = resources.get("open_files_limit")
    nofile_ok = isinstance(ulimits, list) and any(
        isinstance(item, dict)
        and item.get("Name") == "nofile"
        and item.get("Soft") == nofile
        and item.get("Hard") == nofile
        for item in ulimits
    )
    cpu = resources.get("cpu_limit")
    nano_expected = (
        int(float(cpu) * 1_000_000_000)
        if isinstance(cpu, (int, float))
        else -1
    )
    facts: JSONObject = {
        "privileged_false": host.get("Privileged") is False,
        "runtime_runsc": host.get("Runtime") == "runsc",
        "image_exact": config.get("Image") == expected_image,
        "ownership_label": isinstance(labels, dict)
        and labels.get("org.omp.rolebench.owner") == ownership,
        "numeric_user": config.get("User") == expected_user,
        "rootfs_read_only": host.get("ReadonlyRootfs") is True,
        "cap_drop_all": isinstance(cap_drop, list) and set(cap_drop) == {"ALL"},
        "cap_add_empty": cap_add in (None, []),
        "no_new_privileges": isinstance(security, list)
        and set(security)
        in (
            {"no-new-privileges=true"},
            {"no-new-privileges:true"},
            {"no-new-privileges"},
        ),
        "logging_disabled": isinstance(log_config, dict)
        and log_config.get("Type") == "none",
        "network_none": host.get("NetworkMode") == "none",
        "pid_namespace_private": host.get("PidMode") in ("", "private"),
        "ipc_namespace_private": host.get("IpcMode") in ("", "private"),
        "uts_namespace_private": host.get("UTSMode") in ("", "private"),
        "user_namespace_not_host": host.get("UsernsMode") in ("", "private"),
        "cgroup_namespace_private": host.get("CgroupnsMode") in ("", "private"),
        "stdin_open": config.get("OpenStdin") is (stage in {"runner", "verifier"}),
        "mounts_empty": mounts == [],
        "devices_empty": devices in (None, []),
        "cpu_limit": host.get("NanoCpus") == nano_expected,
        "memory_limit": host.get("Memory") == resources.get("memory_bytes"),
        "memory_swap_limit": host.get("MemorySwap") == resources.get("memory_bytes"),
        "pids_limit": host.get("PidsLimit") == resources.get("pids_limit"),
        "nofile_limit": nofile_ok,
        "image_config_exact": inspect.get("Image") == expected_engine_image_id,
    }
    if expected_task_labels is not None:
        observed_task_labels = (
            {
                str(key): str(value)
                for key, value in labels.items()
                if str(key).startswith(_TASK_LABEL_PREFIX)
            }
            if isinstance(labels, dict)
            else {}
        )
        facts["task_labels_preserved"] = (
            observed_task_labels == expected_task_labels
        )
    if stage == "verifier":
        facts["tmpfs_empty"] = tmpfs in (None, {})
    else:
        scratch = _require_object(executor.get("scratch"), "policy.executor.scratch")
        expected = (
            f"rw,nosuid,nodev,noexec,size={scratch.get('size_bytes')},"
            f"uid={user.get('uid')},gid={user.get('gid')},mode=0700"
        )
        facts["workspace_tmpfs"] = isinstance(tmpfs, dict) and tmpfs == {
            "/workspace": expected
        }
    return facts


def _all_true(facts: JSONObject) -> bool:
    return all(value is True for value in facts.values())


def _container_state(inspect: JSONObject) -> tuple[int | None, bool, str]:
    state = inspect.get("State")
    if not isinstance(state, dict):
        return None, False, "unknown"
    code = state.get("ExitCode")
    return (
        code if isinstance(code, int) else None,
        state.get("OOMKilled") is True,
        str(state.get("Status", "unknown")),
    )


def _best_effort(adapter: _Adapter, argv: list[str], *, timeout: float) -> None:
    try:
        _run(adapter, argv, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        pass


def _remove_container(
    adapter: _Adapter,
    docker: str,
    container: str,
    *,
    timeout: float,
) -> bool:
    try:
        result = _run(
            adapter,
            local_docker_argv(docker, "rm", "--force", "--volumes", container),
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _owned_container(
    adapter: _Adapter,
    docker: str,
    name: str,
    ownership: str,
    *,
    timeout: float,
) -> tuple[bool, str | None]:
    try:
        result = _run(
            adapter,
            local_docker_argv(docker, "inspect", name),
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None
    if result.returncode != 0:
        missing = b"no such container" in result.stderr.lower()
        return (True, None) if missing else (False, None)
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError):
        return False, None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return False, None
    inspected = value[0]
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    container_id = inspected.get("Id")
    if (
        not isinstance(labels, dict)
        or labels.get("org.omp.rolebench.owner") != ownership
        or not isinstance(container_id, str)
        or not container_id
    ):
        return False, None
    return True, container_id


def _reconcile_container(
    adapter: _Adapter,
    docker: str,
    name: str,
    ownership: str,
    *,
    timeout: float,
) -> bool:
    established, container = _owned_container(
        adapter,
        docker,
        name,
        ownership,
        timeout=timeout,
    )
    if not established:
        return False
    return container is None or _remove_container(
        adapter,
        docker,
        container,
        timeout=timeout,
    )


def _artifact_file(name: str = "rolebench-artifact") -> BinaryIO:
    flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
    descriptor = os.memfd_create(name, flags)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w+b", buffering=0)


def _seal_file(source: BinaryIO) -> None:
    seals = (
        getattr(fcntl, "F_SEAL_SHRINK", 0)
        | getattr(fcntl, "F_SEAL_GROW", 0)
        | getattr(fcntl, "F_SEAL_WRITE", 0)
        | getattr(fcntl, "F_SEAL_SEAL", 0)
    )
    fcntl.fcntl(source.fileno(), fcntl.F_ADD_SEALS, seals)


def _sealed_memfd_from_bytes(
    data: bytes, name: str = "rolebench-runner-evidence"
) -> BinaryIO:
    file = _artifact_file(name)
    file.write(data)
    file.flush()
    _seal_file(file)
    file.seek(0)
    return file


def _observation(
    *,
    manifest: JSONObject,
    policy_digest: str,
    artifact_digest: str | None,
    runner_evidence_digest: str | None,
    lifecycle: JSONObject,
    stage: str,
    environment: str,
    runner: str,
    issues: list[JSONValue],
    termination_kind: str,
    exit_code: int | None,
    oom_scope: str,
    verifier_outcome: str,
    verifier_valid: bool,
    reward: float | None,
    integrity: str,
    trajectory_facts: JSONObject,
) -> JSONObject:
    run_id = str(manifest["run_id"])
    agent = _require_object(manifest.get("agent"), "agent")
    runner_manifest = _require_object(manifest.get("runner"), "runner")
    verifier = _require_object(manifest.get("verifier"), "verifier")
    task = _require_object(manifest.get("task"), "task")
    config_digest = _sha256(canonical_json(manifest).encode("utf-8"))
    trajectory = _sha256(canonical_json(trajectory_facts).encode("utf-8"))
    digests: JSONObject = {
        "task": task["digest_sha256"],
        "config": config_digest,
        "agent_image": _image_digest(str(agent["image"])),
        "runner_image": _image_digest(str(runner_manifest["image"])),
        "verifier_image": _image_digest(str(verifier["image"])),
        "runtime_policy": policy_digest,
        "artifact": artifact_digest,
        "runner_evidence": runner_evidence_digest,
        "trajectory": trajectory,
        "agent_image_config": agent["config_digest_sha256"],
        "runner_image_config": runner_manifest["config_digest_sha256"],
        "verifier_image_config": verifier["config_digest_sha256"],
    }
    if _manifest_task_binding(manifest) is not None:
        digests.update(
            {
                "task_public_tree": task["public_tree_digest_sha256"],
                "verifier_private_tree": task[
                    "verifier_private_tree_digest_sha256"
                ],
            }
        )
        qualification_digest = task.get("qualification_digest_sha256")
        if isinstance(qualification_digest, str):
            digests["qualification"] = qualification_digest
    observation: JSONObject = {
        "schema_version": "omp.attempt-observation/v2",
        "observation_id": f"{run_id}-observation",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "attempt": {
            "attempt_id": run_id,
            "number": 1,
            "previous_attempt_id": None,
        },
        "stage": stage,
        "lifecycle": lifecycle,
        "readiness": {
            "environment": environment,
            "runner": runner,
            "provider": "unknown",
        },
        "issues": issues,
        "provider": {"request_started": False, "http_status": None},
        "termination": {
            "kind": termination_kind,
            "exit_code": exit_code,
            "signal": None,
            "oom_scope": oom_scope,
        },
        "verifier": {
            "outcome": verifier_outcome,
            "result_valid": verifier_valid,
            "reward": reward,
        },
        "integrity": {"state": integrity},
        "digests": digests,
    }
    if _manifest_task_binding(manifest) is not None:
        observation["evidence_use"] = task["evidence_use"]
    return observation


def _validate_accounting(
    root: Path, observation: JSONObject, outcome: JSONObject
) -> None:
    with tempfile.TemporaryDirectory(prefix="rolebench-worker-") as directory:
        base = Path(directory)
        observation_path = base / "observation.json"
        outcome_path = base / "outcome.json"
        observation_path.write_text(canonical_json(observation), encoding="utf-8")
        outcome_path.write_text(canonical_json(outcome), encoding="utf-8")
        for schema, path in (
            ("attempt-observation", observation_path),
            ("attempt-outcome", outcome_path),
        ):
            result = validate_artifact(root, schema, path)
            if not result.valid:
                raise WorkerError(
                    f"internal {schema} is invalid: {_diagnostics(result)}"
                )


def _creation_issue(stderr: bytes) -> str:
    message = stderr.lower()
    if b"image" in message or b"pull" in message or b"manifest" in message:
        return "image-pull"
    if b"runtime" in message or b"runsc" in message:
        return "runtime-incompatible"
    return "environment-startup"


def run_worker(
    root: Path,
    manifest_path: Path,
    *,
    docker: str = "docker",
    injected_artifact: bytes | None = None,
) -> JSONObject:
    """Execute one supported provider-disabled worker manifest.

    When ``injected_artifact`` is provided, skip the agent container and feed
    those sealed bytes into the existing runner and verifier stages.
    """
    root = Path(root).resolve()
    manifest_path = Path(manifest_path)
    captured_manifest = _capture_object(
        root,
        manifest_path,
        label="worker run manifest",
    )
    manifest_probe = captured_manifest[0]
    if manifest_probe.get("schema_version") == "omp.worker-run-manifest/v1":
        if injected_artifact is not None:
            raise WorkerError(
                "injected artifacts require omp.worker-run-manifest/v2"
            )
        from . import worker_v1

        adapter_factory = (
            _ADAPTER_FACTORY
            if _ADAPTER_FACTORY is not _SubprocessAdapter
            else None
        )
        return worker_v1.run_worker_v1(
            root,
            captured_manifest,
            docker=docker,
            adapter_factory=adapter_factory,
        )
    (
        manifest,
        policy_path,
        policy,
        policy_digest,
        manifest_identity,
        policy_identity,
    ) = _load_manifest(root, captured_manifest)
    doctor = doctor_worker(root, policy_path, docker=docker)
    adapter = _ADAPTER_FACTORY()
    task_binding = _manifest_task_binding(manifest)
    resources = _require_object(policy.get("resources"), "policy.resources")
    timeouts = _require_object(policy.get("timeouts"), "policy.timeouts")
    output_limit = int(resources["output_bytes_limit"])
    if output_limit > 64 * 1024 * 1024:
        raise WorkerError("output_bytes_limit exceeds the 64 MiB host-safe ceiling")
    cleanup_limit = float(timeouts["termination_grace_seconds"])
    execution_deadline = (
        time.monotonic()
        + float(timeouts["total_seconds"])
        - cleanup_limit
    )
    ownership = os.urandom(32).hex()
    attempt_nonce = secrets.token_hex(32)
    run_id = str(manifest["run_id"])
    task_digest = str(_require_object(manifest.get("task"), "task")["digest_sha256"])

    agent = _require_object(manifest.get("agent"), "agent")
    runner_manifest = _require_object(manifest.get("runner"), "runner")
    verifier = _require_object(manifest.get("verifier"), "verifier")
    agent_image = str(agent["image"])
    runner_image = str(runner_manifest["image"])
    verifier_image = str(verifier["image"])
    agent_digest = _image_digest(agent_image)
    runner_digest = _image_digest(runner_image)
    verifier_digest = _image_digest(verifier_image)
    engine_image_ids: dict[str, str] = {}
    agent_argv = list(agent["argv"])
    runner_argv = list(runner_manifest["argv"])
    verifier_argv = list(verifier["argv"])

    evaluation_request_digest = compute_evaluation_request_digest(
        attempt_nonce=attempt_nonce,
        run_id=run_id,
        task_digest_sha256=task_digest,
        runner_manifest=runner_manifest,
        verifier_manifest=verifier,
    )

    lifecycle: JSONObject = {
        "environment_started": False,
        "agent_started": False,
        "agent_finished": False,
        "artifact_frozen": False,
        "runner_started": False,
        "runner_finished": False,
        "runner_evidence_frozen": False,
        "verifier_started": False,
        "verifier_finished": False,
    }
    isolation: JSONObject = {
        "agent": {},
        "runner": {},
        "verifier": {},
        "distinct_images": len({agent_digest, runner_digest, verifier_digest}) == 3,
        "resource_enforcement": doctor.get("resource_enforcement") is True,
        "artifact_frozen_after_agent_exit": False,
        "runner_evidence_frozen_after_runner_exit": False,
        "immutable_agent_runner_handoff": False,
        "immutable_runner_verifier_handoff": False,
    }
    isolation["image_binding"] = {
        "agent": {},
        "runner": {},
        "verifier": {},
        "distinct_config_ids": False,
    }
    isolation["image_binding_verified"] = False

    diagnostics: list[JSONValue] = []
    issues: list[JSONValue] = []
    stage = "environment"
    environment = "failed"
    runner_readiness = "unknown"
    termination_kind = "unknown"
    exit_code: int | None = None
    oom_scope = "none"
    verifier_outcome = "not-run"
    verifier_valid = False
    reward: float | None = None
    integrity = "verified"
    pipeline_passed = False
    artifact: BinaryIO | None = None
    artifact_digest: str | None = None
    runner_evidence_memfd: BinaryIO | None = None
    runner_evidence_digest: str | None = None

    def fail(issue: str, diagnostic: str, *, new_stage: str | None = None) -> None:
        nonlocal stage
        if issue not in issues:
            issues.append(issue)
        diagnostics.append(diagnostic)
        if new_stage is not None:
            stage = new_stage

    try:
        if doctor.get("ready") is not True:
            fail("runtime-incompatible", "worker-doctor-not-ready")
        else:
            environment = "ready"
            runner_readiness = "healthy"
            lifecycle["environment_started"] = True
            agent_name = f"rolebench-agent-{run_id}"
            runner_name = f"rolebench-runner-{run_id}"
            verifier_name = f"rolebench-verifier-{run_id}"

            # Step 1: Preflight inspect and verify OCI image bindings unconditionally
            setup_timeout = _remaining(
                execution_deadline,
                float(timeouts["setup_seconds"]),
            )
            try:
                task_bound = task_binding is not None
                agent_image_inspect = _inspect_image(
                    adapter,
                    docker,
                    agent_image,
                    timeout=_remaining(
                        execution_deadline,
                        setup_timeout,
                    ),
                )
                runner_image_inspect = _inspect_image(
                    adapter,
                    docker,
                    runner_image,
                    timeout=_remaining(
                        execution_deadline,
                        setup_timeout,
                    ),
                )
                verifier_image_inspect = _inspect_image(
                    adapter,
                    docker,
                    verifier_image,
                    timeout=_remaining(
                        execution_deadline,
                        setup_timeout,
                    ),
                )
                for name, inspected_image in (
                    ("agent", agent_image_inspect),
                    ("runner", runner_image_inspect),
                    ("verifier", verifier_image_inspect),
                ):
                    engine_id = inspected_image.get(
                        "_rolebench_engine_image_id"
                    )
                    if not isinstance(engine_id, str):
                        raise _TaskBindingError(
                            "image-engine-id-invalid"
                        )
                    engine_image_ids[name] = engine_id
                agent_image_facts = _image_binding_facts(
                    agent_image_inspect,
                    manifest,
                    agent,
                    stage="agent",
                    task_bound=task_bound,
                )
                runner_image_facts = _image_binding_facts(
                    runner_image_inspect,
                    manifest,
                    runner_manifest,
                    stage="runner",
                    task_bound=task_bound,
                )
                verifier_image_facts = _image_binding_facts(
                    verifier_image_inspect,
                    manifest,
                    verifier,
                    stage="verifier",
                    task_bound=task_bound,
                )
                binding_report = _require_object(
                    isolation["image_binding"],
                    "image binding",
                )
                binding_report["agent"] = agent_image_facts
                binding_report["runner"] = runner_image_facts
                binding_report["verifier"] = verifier_image_facts
                distinct_configs = (
                    len(
                        {
                            agent["config_digest_sha256"],
                            runner_manifest["config_digest_sha256"],
                            verifier["config_digest_sha256"],
                        }
                    )
                    == 3
                )
                binding_report["distinct_config_ids"] = distinct_configs
                if (
                    not _all_true(agent_image_facts)
                    or not _all_true(runner_image_facts)
                    or not _all_true(verifier_image_facts)
                    or not distinct_configs
                ):
                    raise _TaskBindingError("image-binding-mismatch")
                isolation["image_binding_verified"] = True
            except _TaskBindingError:
                fail("sandbox-violation", "image-binding-mismatch")
                runner_readiness = "failed"
            except subprocess.TimeoutExpired:
                fail("runner-failure", "image-inspect-timeout")
                termination_kind = "orchestrator-timeout"
                runner_readiness = "failed"
            except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
                fail(
                    "runner-failure",
                    f"image-inspect-failure:{type(exc).__name__}",
                )
                runner_readiness = "failed"

            # Step 2: Agent Execution, or sealed live-artifact injection.
            if not issues and injected_artifact is not None:
                artifact_limit = min(
                    int(resources["artifact_bytes_limit"]),
                    output_limit,
                )
                if len(injected_artifact) > artifact_limit:
                    fail(
                        "artifact-collection",
                        "artifact-size-limit-exceeded",
                        new_stage="artifact",
                    )
                else:
                    artifact = _sealed_memfd_from_bytes(
                        injected_artifact,
                        "rolebench-injected-artifact",
                    )
                    artifact_digest = hashlib.sha256(injected_artifact).hexdigest()
                    lifecycle["agent_started"] = True
                    lifecycle["agent_finished"] = True
                    lifecycle["artifact_frozen"] = True
                    isolation["artifact_frozen_after_agent_exit"] = True
                    isolation["agent"] = {}
                    exit_code = 0
                    stage = "artifact"
            elif not issues:
                agent_id: str | None = None
                agent_create_uncertain = False
                try:
                    try:
                        created = _run(
                            adapter,
                            _create_args(
                                docker,
                                agent_name,
                                agent_image,
                                agent_argv,
                                policy,
                                stage="agent",
                                ownership=ownership,
                            ),
                            timeout=setup_timeout,
                        )
                    except (OSError, subprocess.SubprocessError):
                        agent_create_uncertain = True
                        raise
                    if created.returncode != 0:
                        fail(
                            _creation_issue(created.stderr),
                            "agent-container-create-failed",
                        )
                        runner_readiness = "failed"
                    else:
                        agent_id = created.stdout.decode("utf-8", "replace").strip()
                        if not agent_id:
                            agent_create_uncertain = True
                            fail("runner-failure", "agent-container-id-missing")
                            runner_readiness = "failed"
                        else:
                            inspected = _inspect(
                                adapter,
                                docker,
                                agent_id,
                                _remaining(execution_deadline, setup_timeout),
                            )
                            if inspected.get("Id") != agent_id:
                                fail(
                                    "sandbox-violation",
                                    "agent-container-id-mismatch",
                                )
                            agent_facts = _isolation_facts(
                                inspected,
                                policy,
                                stage="agent",
                                expected_image=agent_image,
                                ownership=ownership,
                                expected_engine_image_id=engine_image_ids[
                                    "agent"
                                ],
                                expected_task_labels=(
                                    _expected_task_labels(
                                        manifest,
                                        stage="agent",
                                    )
                                    if task_binding is not None
                                    else None
                                ),
                            )
                            isolation["agent"] = agent_facts
                            if not _all_true(agent_facts):
                                fail(
                                    "sandbox-violation",
                                    "agent-effective-isolation-mismatch",
                                )
                                runner_readiness = "failed"
                            else:
                                lifecycle["agent_started"] = True
                                stage = "agent"
                                artifact_limit = min(
                                    int(resources["artifact_bytes_limit"]),
                                    output_limit,
                                )
                                artifact = _artifact_file("rolebench-artifact")
                                streamed = adapter.stream(
                                    local_docker_argv(
                                        docker,
                                        "start",
                                        "--attach",
                                        agent_id,
                                    ),
                                    input_source=None,
                                    timeout=_remaining(
                                        execution_deadline,
                                        float(timeouts["agent_seconds"]),
                                    ),
                                    output_limit=artifact_limit,
                                    output_sink=artifact,
                                )
                                if streamed.timed_out:
                                    _best_effort(
                                        adapter,
                                        local_docker_argv(docker, "kill", agent_id),
                                        timeout=cleanup_limit,
                                    )
                                    fail("runner-failure", "agent-timeout")
                                    termination_kind = "orchestrator-timeout"
                                elif streamed.overflowed:
                                    _best_effort(
                                        adapter,
                                        local_docker_argv(docker, "kill", agent_id),
                                        timeout=cleanup_limit,
                                    )
                                    fail(
                                        "artifact-collection",
                                        "artifact-size-limit-exceeded",
                                        new_stage="artifact",
                                    )
                                inspected = _inspect(
                                    adapter,
                                    docker,
                                    agent_id,
                                    _remaining(execution_deadline, cleanup_limit),
                                )
                                state_exit, oom, status = _container_state(inspected)
                                exit_code = state_exit
                                lifecycle["agent_finished"] = status in {
                                    "exited",
                                    "dead",
                                }
                                if oom:
                                    fail("runner-failure", "agent-oom")
                                    termination_kind = "resource-limit"
                                    oom_scope = "attempt"
                                elif (
                                    not streamed.timed_out
                                    and not streamed.overflowed
                                    and state_exit in {126, 127}
                                ):
                                    fail(
                                        "broken-entrypoint",
                                        "agent-entrypoint-failed",
                                    )
                                elif (
                                    not streamed.timed_out
                                    and not streamed.overflowed
                                    and (
                                        state_exit != 0
                                        or streamed.returncode != 0
                                        or not lifecycle["agent_finished"]
                                    )
                                ):
                                    fail(
                                        "runner-failure",
                                        "agent-nonzero-or-not-exited",
                                    )
                                elif not issues:
                                    artifact_deadline = min(
                                        execution_deadline,
                                        time.monotonic()
                                        + float(timeouts["artifact_seconds"]),
                                    )
                                    artifact_digest = _sha256_stream(artifact)
                                    if _remaining(artifact_deadline, 1) <= 0:
                                        raise subprocess.TimeoutExpired(
                                            "artifact-freeze",
                                            float(timeouts["artifact_seconds"]),
                                        )
                                    _seal_file(artifact)
                                    artifact.seek(0)
                                    lifecycle["artifact_frozen"] = True
                                    isolation[
                                        "artifact_frozen_after_agent_exit"
                                    ] = lifecycle["agent_finished"] is True
                                    stage = "artifact"
                except subprocess.TimeoutExpired:
                    agent_create_uncertain = agent_id is None
                    fail("runner-failure", "agent-setup-timeout")
                    termination_kind = "orchestrator-timeout"
                    runner_readiness = "failed"
                except (
                    OSError,
                    RuntimeError,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as exc:
                    fail(
                        "runner-failure",
                        f"agent-runtime-failure:{type(exc).__name__}",
                    )
                    runner_readiness = "failed"
                finally:
                    cleaned = (
                        _remove_container(
                            adapter,
                            docker,
                            agent_id,
                            timeout=cleanup_limit,
                        )
                        if agent_id is not None
                        else _reconcile_container(
                            adapter,
                            docker,
                            agent_name,
                            ownership,
                            timeout=cleanup_limit,
                        )
                        if agent_create_uncertain
                        else True
                    )
                    if not cleaned:
                        fail("runner-failure", "agent-container-cleanup-failed")

            # Step 3: Candidate Runner Execution
            if (
                artifact is not None
                and lifecycle["artifact_frozen"]
                and not issues
            ):
                runner_id: str | None = None
                runner_create_uncertain = False
                runner_removed = False
                try:
                    runner_setup_timeout = _remaining(
                        execution_deadline,
                        float(timeouts["setup_seconds"]),
                    )
                    try:
                        created = _run(
                            adapter,
                            _create_args(
                                docker,
                                runner_name,
                                runner_image,
                                runner_argv,
                                policy,
                                stage="runner",
                                ownership=ownership,
                            ),
                            timeout=runner_setup_timeout,
                        )
                    except (OSError, subprocess.SubprocessError):
                        runner_create_uncertain = True
                        raise
                    if created.returncode != 0:
                        fail(
                            _creation_issue(created.stderr),
                            "runner-container-create-failed",
                            new_stage="runner",
                        )
                    else:
                        runner_id = created.stdout.decode("utf-8", "replace").strip()
                        if not runner_id:
                            runner_create_uncertain = True
                            fail(
                                "runner-failure",
                                "runner-container-id-missing",
                                new_stage="runner",
                            )
                        else:
                            inspected = _inspect(
                                adapter,
                                docker,
                                runner_id,
                                _remaining(
                                    execution_deadline,
                                    runner_setup_timeout,
                                ),
                            )
                            if inspected.get("Id") != runner_id:
                                fail(
                                    "sandbox-violation",
                                    "runner-container-id-mismatch",
                                    new_stage="runner",
                                )
                            runner_facts = _isolation_facts(
                                inspected,
                                policy,
                                stage="runner",
                                expected_image=runner_image,
                                ownership=ownership,
                                expected_engine_image_id=engine_image_ids[
                                    "runner"
                                ],
                                expected_task_labels=(
                                    _expected_task_labels(
                                        manifest,
                                        stage="runner",
                                    )
                                    if task_binding is not None
                                    else None
                                ),
                            )
                            isolation["runner"] = runner_facts
                            if not _all_true(runner_facts):
                                fail(
                                    "sandbox-violation",
                                    "runner-effective-isolation-mismatch",
                                    new_stage="runner",
                                )
                            else:
                                lifecycle["runner_started"] = True
                                stage = "runner"
                                isolation["immutable_agent_runner_handoff"] = (
                                    _sha256_stream(artifact) == artifact_digest
                                )
                                artifact.seek(0)
                                runner_seconds = float(
                                    timeouts.get("runner_seconds", 900)
                                )
                                runner_start_monotonic = time.monotonic()
                                runner_streamed = adapter.stream(
                                    local_docker_argv(
                                        docker,
                                        "start",
                                        "--attach",
                                        "-i",
                                        runner_id,
                                    ),
                                    input_source=artifact,
                                    timeout=_remaining(
                                        execution_deadline,
                                        runner_seconds,
                                    ),
                                    output_limit=output_limit,
                                )
                                runner_duration_seconds = max(
                                    0.0,
                                    time.monotonic() - runner_start_monotonic,
                                )
                                if runner_streamed.timed_out:
                                    _best_effort(
                                        adapter,
                                        local_docker_argv(
                                            docker, "kill", runner_id
                                        ),
                                        timeout=cleanup_limit,
                                    )
                                    fail("runner-failure", "runner-timeout")
                                    termination_kind = "orchestrator-timeout"
                                elif runner_streamed.overflowed:
                                    _best_effort(
                                        adapter,
                                        local_docker_argv(
                                            docker, "kill", runner_id
                                        ),
                                        timeout=cleanup_limit,
                                    )
                                    fail(
                                        "runner-failure",
                                        "runner-output-size-limit-exceeded",
                                    )
                                inspected = _inspect(
                                    adapter,
                                    docker,
                                    runner_id,
                                    _remaining(
                                        execution_deadline, cleanup_limit
                                    ),
                                )
                                (
                                    runner_state_exit,
                                    runner_oom,
                                    runner_status,
                                ) = _container_state(inspected)
                                lifecycle["runner_finished"] = runner_status in {
                                    "exited",
                                    "dead",
                                }
                                if runner_oom:
                                    fail("runner-failure", "runner-oom")
                                    termination_kind = "resource-limit"
                                    oom_scope = "attempt"
                                elif (
                                    not runner_streamed.timed_out
                                    and not runner_streamed.overflowed
                                    and runner_state_exit in {126, 127}
                                ):
                                    fail(
                                        "broken-entrypoint",
                                        "runner-entrypoint-failed",
                                    )
                                elif (
                                    not runner_streamed.timed_out
                                    and not runner_streamed.overflowed
                                    and (
                                        runner_state_exit != 0
                                        or runner_streamed.returncode != 0
                                        or not lifecycle["runner_finished"]
                                    )
                                ):
                                    fail(
                                        "runner-failure",
                                        "runner-nonzero-or-not-exited",
                                    )

                                # Remove container and verify certain removal before building evidence
                                runner_removed = _remove_container(
                                    adapter,
                                    docker,
                                    runner_id,
                                    timeout=cleanup_limit,
                                )
                                if not runner_removed:
                                    fail(
                                        "runner-failure",
                                        "runner-container-cleanup-failed",
                                    )

                                # Build and freeze runner evidence only if no issues and certain removal
                                if not issues and runner_removed:
                                    runner_evidence_bytes = build_runner_evidence(
                                        attempt_nonce=attempt_nonce,
                                        run_id=run_id,
                                        task_digest_sha256=task_digest,
                                        policy_digest_sha256=policy_digest,
                                        artifact_digest_sha256=str(
                                            artifact_digest
                                        ),
                                        evaluation_request_digest_sha256=evaluation_request_digest,
                                        verifier_image_digest_sha256=verifier_digest,
                                        runner_manifest=runner_manifest,
                                        container_id=runner_id,
                                        state=runner_status,
                                        exit_code=runner_state_exit,
                                        oom_killed=runner_oom,
                                        timed_out=runner_streamed.timed_out,
                                        overflowed=runner_streamed.overflowed,
                                        duration_seconds=runner_duration_seconds,
                                        removed=runner_removed,
                                        stdout=runner_streamed.stdout,
                                        stderr=runner_streamed.stderr,
                                    )
                                    runner_evidence_digest = hashlib.sha256(
                                        runner_evidence_bytes
                                    ).hexdigest()
                                    runner_evidence_memfd = (
                                        _sealed_memfd_from_bytes(
                                            runner_evidence_bytes,
                                            "rolebench-runner-evidence",
                                        )
                                    )
                                    lifecycle["runner_evidence_frozen"] = True
                                    isolation[
                                        "runner_evidence_frozen_after_runner_exit"
                                    ] = (
                                        lifecycle["runner_finished"] is True
                                        and runner_removed
                                    )
                                    stage = "runner-evidence"
                except subprocess.TimeoutExpired:
                    runner_create_uncertain = runner_id is None
                    fail(
                        "runner-failure",
                        "runner-setup-timeout",
                        new_stage="runner",
                    )
                    termination_kind = "orchestrator-timeout"
                except (
                    OSError,
                    RuntimeError,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as exc:
                    fail(
                        "runner-failure",
                        f"runner-runtime-failure:{type(exc).__name__}",
                        new_stage="runner",
                    )
                finally:
                    if not runner_removed:
                        cleaned = (
                            _remove_container(
                                adapter,
                                docker,
                                runner_id,
                                timeout=cleanup_limit,
                            )
                            if runner_id is not None
                            else _reconcile_container(
                                adapter,
                                docker,
                                runner_name,
                                ownership,
                                timeout=cleanup_limit,
                            )
                            if runner_create_uncertain
                            else True
                        )
                        if not cleaned:
                            fail(
                                "runner-failure",
                                "runner-container-cleanup-failed",
                            )

            # Step 4: Passive Verifier Execution
            if (
                runner_evidence_memfd is not None
                and lifecycle["runner_evidence_frozen"]
                and not issues
            ):
                verifier_id: str | None = None
                verifier_create_uncertain = False
                try:
                    verifier_setup_timeout = _remaining(
                        execution_deadline,
                        float(timeouts["setup_seconds"]),
                    )
                    try:
                        created = _run(
                            adapter,
                            _create_args(
                                docker,
                                verifier_name,
                                verifier_image,
                                verifier_argv,
                                policy,
                                stage="verifier",
                                ownership=ownership,
                            ),
                            timeout=verifier_setup_timeout,
                        )
                    except (OSError, subprocess.SubprocessError):
                        verifier_create_uncertain = True
                        raise
                    if created.returncode != 0:
                        fail(
                            _creation_issue(created.stderr),
                            "verifier-container-create-failed",
                            new_stage="verifier",
                        )
                    else:
                        verifier_id = created.stdout.decode(
                            "utf-8", "replace"
                        ).strip()
                        if not verifier_id:
                            verifier_create_uncertain = True
                            fail(
                                "verifier-crash",
                                "verifier-container-id-missing",
                                new_stage="verifier",
                            )
                        else:
                            inspected = _inspect(
                                adapter,
                                docker,
                                verifier_id,
                                _remaining(
                                    execution_deadline,
                                    cleanup_limit,
                                ),
                            )
                            if inspected.get("Id") != verifier_id:
                                fail(
                                    "sandbox-violation",
                                    "verifier-container-id-mismatch",
                                    new_stage="verifier",
                                )
                            verifier_facts = _isolation_facts(
                                inspected,
                                policy,
                                stage="verifier",
                                expected_image=verifier_image,
                                ownership=ownership,
                                expected_engine_image_id=engine_image_ids[
                                    "verifier"
                                ],
                                expected_task_labels=(
                                    _expected_task_labels(
                                        manifest,
                                        stage="verifier",
                                    )
                                    if task_binding is not None
                                    else None
                                ),
                            )
                            isolation["verifier"] = verifier_facts
                            if not _all_true(verifier_facts):
                                fail(
                                    "sandbox-violation",
                                    "verifier-effective-isolation-mismatch",
                                    new_stage="verifier",
                                )
                            else:
                                lifecycle["verifier_started"] = True
                                stage = "verifier"
                                isolation[
                                    "immutable_runner_verifier_handoff"
                                ] = (
                                    _sha256_stream(runner_evidence_memfd)
                                    == runner_evidence_digest
                                )
                                runner_evidence_memfd.seek(0)
                                streamed = adapter.stream(
                                    local_docker_argv(
                                        docker,
                                        "start",
                                        "--attach",
                                        "-i",
                                        verifier_id,
                                    ),
                                    input_source=runner_evidence_memfd,
                                    timeout=_remaining(
                                        execution_deadline,
                                        float(timeouts["verifier_seconds"]),
                                    ),
                                    output_limit=min(output_limit, 1024 * 1024),
                                )
                                if streamed.timed_out:
                                    _best_effort(
                                        adapter,
                                        local_docker_argv(
                                            docker,
                                            "kill",
                                            verifier_id,
                                        ),
                                        timeout=cleanup_limit,
                                    )
                                    fail("verifier-crash", "verifier-timeout")
                                elif streamed.overflowed:
                                    _best_effort(
                                        adapter,
                                        local_docker_argv(
                                            docker,
                                            "kill",
                                            verifier_id,
                                        ),
                                        timeout=cleanup_limit,
                                    )
                                    fail(
                                        "verifier-result-malformed",
                                        "verifier-output-size-limit-exceeded",
                                    )
                                inspected = _inspect(
                                    adapter,
                                    docker,
                                    verifier_id,
                                    cleanup_limit,
                                )
                                (
                                    verifier_state_exit,
                                    verifier_oom,
                                    verifier_status,
                                ) = _container_state(inspected)
                                lifecycle["verifier_finished"] = (
                                    verifier_status in {"exited", "dead"}
                                )
                                if (
                                    not streamed.timed_out
                                    and not streamed.overflowed
                                    and (
                                        verifier_oom
                                        or verifier_state_exit != 0
                                        or streamed.returncode != 0
                                    )
                                ):
                                    fail(
                                        "verifier-crash",
                                        "verifier-nonzero-or-oom",
                                    )
                                elif not lifecycle["verifier_finished"]:
                                    fail(
                                        "verifier-result-missing",
                                        "verifier-did-not-exit",
                                    )
                                elif not issues:
                                    try:
                                        raw_json = parse_verifier_result(
                                            streamed.stdout
                                        )
                                        (
                                            v_outcome,
                                            v_reward,
                                        ) = validate_verifier_result(
                                            raw_json,
                                            expected_run_id=run_id,
                                            expected_attempt_nonce=attempt_nonce,
                                            expected_artifact_digest_sha256=str(
                                                artifact_digest
                                            ),
                                            expected_runner_evidence_digest_sha256=str(
                                                runner_evidence_digest
                                            ),
                                            expected_evaluation_request_digest_sha256=evaluation_request_digest,
                                            expected_verifier_image_digest_sha256=verifier_digest,
                                        )
                                        if v_outcome == "error":
                                            fail(
                                                "verifier-crash",
                                                "verifier-reported-error",
                                            )
                                        else:
                                            verifier_outcome = v_outcome
                                            reward = v_reward
                                            verifier_valid = True
                                            pipeline_passed = True
                                            stage = "complete"
                                            termination_kind = "completed"
                                    except ProtocolError as exc:
                                        issue = (
                                            "verifier-result-missing"
                                            if not streamed.stdout.strip()
                                            else "verifier-result-malformed"
                                        )
                                        fail(
                                            issue,
                                            f"verifier-protocol-error:{exc}",
                                        )
                except subprocess.TimeoutExpired:
                    verifier_create_uncertain = verifier_id is None
                    fail(
                        "verifier-crash",
                        "verifier-setup-timeout",
                        new_stage="verifier",
                    )
                except (
                    OSError,
                    RuntimeError,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as exc:
                    fail(
                        "verifier-crash",
                        f"verifier-runtime-failure:{type(exc).__name__}",
                        new_stage="verifier",
                    )
                finally:
                    cleaned = (
                        _remove_container(
                            adapter,
                            docker,
                            verifier_id,
                            timeout=cleanup_limit,
                        )
                        if verifier_id is not None
                        else _reconcile_container(
                            adapter,
                            docker,
                            verifier_name,
                            ownership,
                            timeout=cleanup_limit,
                        )
                        if verifier_create_uncertain
                        else True
                    )
                    if not cleaned:
                        fail(
                            "runner-failure",
                            "verifier-container-cleanup-failed",
                        )

        if lifecycle["verifier_started"] and not verifier_valid:
            verifier_outcome = "error"
        if termination_kind == "unknown" and issues:
            termination_kind = (
                "completed" if lifecycle["agent_finished"] else "unknown"
            )
        _verify_capture_stable(
            root,
            manifest_identity[0],
            manifest_identity[1],
            label="worker run manifest",
        )
        _verify_capture_stable(
            root,
            policy_identity[0],
            policy_identity[1],
            label="scored-worker policy",
        )
        trajectory_facts: JSONObject = {
            "stage": stage,
            "lifecycle": lifecycle,
            "issues": issues,
            "termination_kind": termination_kind,
            "agent_exit_code": exit_code,
            "artifact_digest": artifact_digest,
            "runner_evidence_digest": runner_evidence_digest,
            "isolation": isolation,
        }
        observation = _observation(
            manifest=manifest,
            policy_digest=policy_digest,
            artifact_digest=artifact_digest,
            runner_evidence_digest=runner_evidence_digest,
            lifecycle=lifecycle,
            stage=stage,
            environment=environment,
            runner=runner_readiness,
            issues=issues,
            termination_kind=termination_kind,
            exit_code=exit_code,
            oom_scope=oom_scope,
            verifier_outcome=verifier_outcome,
            verifier_valid=verifier_valid,
            reward=reward,
            integrity=integrity,
            trajectory_facts=trajectory_facts,
        )
        try:
            outcome = classify_attempt(observation)
        except AccountingError as exc:
            raise WorkerError(
                f"cannot classify worker observation: {exc}"
            ) from exc
        _validate_accounting(root, observation, outcome)
        if outcome.get("disposition") == "scored":
            raise WorkerError(
                "provider-disabled execution was unexpectedly scored"
            )
        return {
            "schema_version": "omp.worker-run-report/v1",
            "run_id": manifest["run_id"],
            "passed": pipeline_passed
            and not issues
            and _all_true(
                _require_object(isolation["agent"], "agent isolation")
            )
            and _all_true(
                _require_object(isolation["runner"], "runner isolation")
            )
            and _all_true(
                _require_object(isolation["verifier"], "verifier isolation")
            )
            and isolation["distinct_images"] is True
            and isolation["resource_enforcement"] is True
            and isolation["image_binding_verified"] is True
            and isolation["artifact_frozen_after_agent_exit"] is True
            and isolation["runner_evidence_frozen_after_runner_exit"] is True
            and isolation["immutable_agent_runner_handoff"] is True
            and isolation["immutable_runner_verifier_handoff"] is True,
            "external_provider_calls": 0,
            "policy_digest_sha256": policy_digest,
            "artifact_digest_sha256": artifact_digest,
            "runner_evidence_digest_sha256": runner_evidence_digest,
            "observation": observation,
            "outcome": outcome,
            "doctor": doctor,
            "isolation": isolation,
            "diagnostics": diagnostics,
        }
    finally:
        if artifact is not None:
            artifact.close()
        if runner_evidence_memfd is not None:
            runner_evidence_memfd.close()
