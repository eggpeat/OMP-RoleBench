"""Fail-closed Docker/runsc execution for provider-disabled scored workers."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Callable, Protocol

from .accounting import AccountingError, classify_attempt
from .contracts import JSONObject, JSONValue, canonical_json, validate_artifact


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
        self, argv: list[str], *, timeout: float | None = None, input_bytes: bytes | None = None
    ) -> _CommandResult: ...

    def stream(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None,
        timeout: float,
        output_limit: int,
    ) -> _StreamResult: ...


class _SubprocessAdapter:
    """Small argv-only subprocess boundary, replaceable by deterministic tests."""

    _LOG_LIMIT = 64 * 1024

    def run(
        self, argv: list[str], *, timeout: float | None = None, input_bytes: bytes | None = None
    ) -> _CommandResult:
        completed = subprocess.run(
            argv,
            input=input_bytes,
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
        input_bytes: bytes | None,
        timeout: float,
        output_limit: int,
    ) -> _StreamResult:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
        )
        assert process.stdout is not None and process.stderr is not None
        if input_bytes is not None:
            def feed_stdin() -> None:
                assert process.stdin is not None
                try:
                    process.stdin.write(input_bytes)
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
        deadline = time.monotonic() + timeout
        timed_out = False
        overflowed = False
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            events = selector.select(min(remaining, 0.1))
            if not events and process.poll() is not None:
                # Pipes may still contain buffered data; keep selecting until EOF.
                continue
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    if len(out) + len(chunk) > output_limit:
                        keep = max(0, output_limit - len(out))
                        out.extend(chunk[:keep])
                        overflowed = True
                        process.kill()
                        break
                    out.extend(chunk)
                elif len(err) < self._LOG_LIMIT:
                    err.extend(chunk[: self._LOG_LIMIT - len(err)])
            if timed_out or overflowed:
                break
        selector.close()
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait()
        return _StreamResult(returncode, bytes(out), bytes(err), timed_out, overflowed)


_ADAPTER_FACTORY: Callable[[], _Adapter] = _SubprocessAdapter


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _diagnostics(result: object) -> str:
    diagnostics = getattr(result, "diagnostics", ())
    return "; ".join(
        f"{item.file}:{item.json_path}: {item.message}" for item in diagnostics
    )


def _safe_relative(root: Path, raw: str, *, label: str) -> Path:
    root = root.resolve()
    candidate = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkerError(f"{label} must remain inside the repository") from exc
    return candidate


def _load_object(path: Path, *, label: str) -> JSONObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkerError(f"cannot load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerError(f"{label} must be a JSON object")
    return value


def _validated_policy(root: Path, path: Path) -> tuple[JSONObject, str]:
    validation = validate_artifact(root, "scored-worker-policy", path)
    if not validation.valid:
        raise WorkerError(f"invalid scored-worker policy: {_diagnostics(validation)}")
    policy = _load_object(path, label="scored-worker policy")
    digest = _sha256(canonical_json(policy).encode("utf-8"))
    return policy, digest


def _docker_info(adapter: _Adapter, docker: str) -> tuple[_CommandResult | None, JSONObject | None, str | None]:
    try:
        result = adapter.run([docker, "info", "--format", "{{json .}}"], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
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


def doctor_worker(root: Path, policy_path: Path, *, docker: str = "docker") -> JSONObject:
    """Report whether the exact rootless Docker/runsc worker prerequisites are ready."""

    root = Path(root).resolve()
    diagnostics: list[JSONValue] = []
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
    option_strings: list[str] = []
    if isinstance(security_options, list):
        for option in security_options:
            if isinstance(option, str):
                option_strings.append(option)
            elif isinstance(option, dict):
                option_strings.extend(str(value) for value in option.values())
    rootless = any("rootless" in option.lower() for option in option_strings)
    runtimes = info.get("Runtimes", {}) if info else {}
    runsc = isinstance(runtimes, dict) and "runsc" in runtimes
    cgroup_v2 = bool(info) and str(info.get("CgroupVersion")) == "2"
    delegation = cgroup_v2 and bool(info) and info.get("CgroupDriver") == "systemd"

    checks = {
        "docker-executable": executable,
        "docker-server": server,
        "rootless": rootless,
        "runsc": runsc,
        "cgroup-v2": cgroup_v2,
        "delegation": delegation,
        "policy": policy_valid,
    }
    diagnostics.extend(f"requirement-failed:{name}" for name, passed in checks.items() if not passed)
    ready = all(checks.values())
    return {
        "schema_version": "omp.worker-doctor-report/v1",
        "ready": ready,
        "docker_executable": executable,
        "docker_server": server,
        "rootless": rootless,
        "runsc": runsc,
        "cgroup_v2": cgroup_v2,
        "delegation": delegation,
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


def _load_manifest(root: Path, manifest_path: Path) -> tuple[JSONObject, Path, JSONObject, str]:
    manifest_file = _safe_relative(root, str(manifest_path), label="manifest path")
    validation = validate_artifact(root, "worker-run-manifest", manifest_file)
    if not validation.valid:
        raise WorkerError(f"invalid worker run manifest: {_diagnostics(validation)}")
    manifest = _load_object(manifest_file, label="worker run manifest")
    provider = _require_object(manifest.get("provider"), "provider")
    if provider != {"enabled": False}:
        raise WorkerError("worker provider must be exactly disabled")
    policy_ref = _require_object(manifest.get("policy"), "policy")
    raw_path = policy_ref.get("path")
    expected_digest = policy_ref.get("digest_sha256")
    if not isinstance(raw_path, str) or not isinstance(expected_digest, str):
        raise WorkerError("manifest policy reference is invalid")
    policy_path = _safe_relative(root, raw_path, label="policy path")
    policy, policy_digest = _validated_policy(root, policy_path)
    if policy_digest != expected_digest:
        raise WorkerError("manifest policy digest does not match canonical policy")
    agent = _require_object(manifest.get("agent"), "agent")
    verifier = _require_object(manifest.get("verifier"), "verifier")
    if agent.get("image") == verifier.get("image"):
        raise WorkerError("agent and verifier images must differ")
    for participant in (agent, verifier):
        digest = _image_digest(str(participant.get("image", "")))
        if digest == "0" * 64:
            raise WorkerError("placeholder image digests must be replaced before execution")
    return manifest, policy_path, policy, policy_digest


def _remaining(deadline: float, cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(remaining, cap)


def _run(adapter: _Adapter, argv: list[str], *, timeout: float) -> _CommandResult:
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
    verifier: bool,
) -> list[str]:
    executor = _require_object(policy.get("executor"), "policy.executor")
    resources = _require_object(policy.get("resources"), "policy.resources")
    principal = _require_object(
        _require_object(policy.get("verifier"), "policy.verifier").get("user")
        if verifier
        else executor.get("user"),
        "container user",
    )
    uid, gid = principal.get("uid"), principal.get("gid")
    if not isinstance(uid, int) or not isinstance(gid, int):
        raise WorkerError("container uid and gid must be numeric")
    args = [
        docker, "create", "--name", name,
        "--runtime", "runsc",
        "--user", f"{uid}:{gid}",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges=true",
        "--network", "none",
    ]
    if not verifier:
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
        "--cpus", str(cpu),
        "--memory", str(memory),
        "--memory-swap", str(memory),
        "--pids-limit", str(pids),
        "--ulimit", f"nofile={nofile}:{nofile}",
        image,
        *argv,
    ]
    return args


def _inspect(adapter: _Adapter, docker: str, container: str, timeout: float) -> JSONObject:
    result = _run(adapter, [docker, "inspect", container], timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError("container-inspect-failed")
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("container-inspect-malformed") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError("container-inspect-malformed")
    return value[0]


def _isolation_facts(inspect: JSONObject, policy: JSONObject, *, verifier: bool) -> JSONObject:
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
        if verifier
        else executor.get("user"),
        "container user",
    )
    expected_user = f"{user.get('uid')}:{user.get('gid')}"
    cap_drop = host.get("CapDrop")
    cap_add = host.get("CapAdd")
    security = host.get("SecurityOpt")
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
    nano_expected = int(float(cpu) * 1_000_000_000) if isinstance(cpu, (int, float)) else -1
    facts: JSONObject = {
        "runtime_runsc": host.get("Runtime") == "runsc",
        "numeric_user": config.get("User") == expected_user,
        "rootfs_read_only": host.get("ReadonlyRootfs") is True,
        "cap_drop_all": isinstance(cap_drop, list) and set(cap_drop) == {"ALL"},
        "cap_add_empty": cap_add in (None, []),
        "no_new_privileges": isinstance(security, list)
        and set(security) in (
            {"no-new-privileges=true"},
            {"no-new-privileges:true"},
            {"no-new-privileges"},
        ),
        "network_none": host.get("NetworkMode") == "none",
        "mounts_empty": mounts == [],
        "devices_empty": devices in (None, []),
        "cpu_limit": host.get("NanoCpus") == nano_expected,
        "memory_limit": host.get("Memory") == resources.get("memory_bytes"),
        "memory_swap_limit": host.get("MemorySwap") == resources.get("memory_bytes"),
        "pids_limit": host.get("PidsLimit") == resources.get("pids_limit"),
        "nofile_limit": nofile_ok,
    }
    if verifier:
        facts["tmpfs_empty"] = tmpfs in (None, {})
    else:
        scratch = _require_object(executor.get("scratch"), "policy.executor.scratch")
        expected = (
            f"rw,nosuid,nodev,noexec,size={scratch.get('size_bytes')},"
            f"uid={user.get('uid')},gid={user.get('gid')},mode=0700"
        )
        facts["workspace_tmpfs"] = isinstance(tmpfs, dict) and tmpfs == {"/workspace": expected}
    return facts


def _all_true(facts: JSONObject) -> bool:
    return all(value is True for value in facts.values())


def _container_state(inspect: JSONObject) -> tuple[int | None, bool, str]:
    state = inspect.get("State")
    if not isinstance(state, dict):
        return None, False, "unknown"
    code = state.get("ExitCode")
    return (code if isinstance(code, int) else None, state.get("OOMKilled") is True, str(state.get("Status", "unknown")))


def _best_effort(adapter: _Adapter, argv: list[str]) -> None:
    try:
        adapter.run(argv, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _parse_verifier(data: bytes) -> tuple[str, float] | None:
    try:
        text = data.decode("utf-8")
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(text)
        if text[end:].strip() or not isinstance(value, dict) or set(value) != {"outcome", "reward"}:
            return None
        outcome = value["outcome"]
        reward = value["reward"]
        if isinstance(reward, bool) or not isinstance(reward, (int, float)):
            return None
        if outcome == "accepted" and reward == 1:
            return outcome, float(reward)
        if outcome == "rejected" and 0 <= reward < 1:
            return outcome, float(reward)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        pass
    return None


def _observation(
    *,
    manifest: JSONObject,
    policy_digest: str,
    artifact_digest: str | None,
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
    verifier = _require_object(manifest.get("verifier"), "verifier")
    task = _require_object(manifest.get("task"), "task")
    config_digest = _sha256(canonical_json(manifest).encode("utf-8"))
    trajectory = _sha256(canonical_json(trajectory_facts).encode("utf-8"))
    return {
        "schema_version": "omp.attempt-observation/v1",
        "observation_id": f"{run_id}-observation",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "attempt": {"attempt_id": run_id, "number": 1, "previous_attempt_id": None},
        "stage": stage,
        "lifecycle": lifecycle,
        "readiness": {"environment": environment, "runner": runner, "provider": "unknown"},
        "issues": issues,
        "provider": {"request_started": False, "http_status": None},
        "termination": {"kind": termination_kind, "exit_code": exit_code, "signal": None, "oom_scope": oom_scope},
        "verifier": {"outcome": verifier_outcome, "result_valid": verifier_valid, "reward": reward},
        "integrity": {"state": integrity},
        "digests": {
            "task": task["digest_sha256"],
            "config": config_digest,
            "agent_image": _image_digest(str(agent["image"])),
            "verifier_image": _image_digest(str(verifier["image"])),
            "runtime_policy": policy_digest,
            "artifact": artifact_digest,
            "trajectory": trajectory,
        },
    }


def _validate_accounting(root: Path, observation: JSONObject, outcome: JSONObject) -> None:
    with tempfile.TemporaryDirectory(prefix="rolebench-worker-") as directory:
        base = Path(directory)
        observation_path = base / "observation.json"
        outcome_path = base / "outcome.json"
        observation_path.write_text(canonical_json(observation), encoding="utf-8")
        outcome_path.write_text(canonical_json(outcome), encoding="utf-8")
        for schema, path in (("attempt-observation", observation_path), ("attempt-outcome", outcome_path)):
            result = validate_artifact(root, schema, path)
            if not result.valid:
                raise WorkerError(f"internal {schema} is invalid: {_diagnostics(result)}")


def _creation_issue(stderr: bytes) -> str:
    message = stderr.lower()
    if b"image" in message or b"pull" in message or b"manifest" in message:
        return "image-pull"
    if b"runtime" in message or b"runsc" in message:
        return "runtime-incompatible"
    return "environment-startup"


def run_worker(root: Path, manifest_path: Path, *, docker: str = "docker") -> JSONObject:
    """Execute one provider-disabled worker manifest using rootless Docker/runsc."""

    root = Path(root).resolve()
    manifest, policy_path, policy, policy_digest = _load_manifest(root, Path(manifest_path))
    doctor = doctor_worker(root, policy_path, docker=docker)
    adapter = _ADAPTER_FACTORY()
    resources = _require_object(policy.get("resources"), "policy.resources")
    timeouts = _require_object(policy.get("timeouts"), "policy.timeouts")
    total_deadline = time.monotonic() + float(timeouts["total_seconds"])
    lifecycle: JSONObject = {
        "environment_started": False,
        "agent_started": False,
        "agent_finished": False,
        "artifact_frozen": False,
        "verifier_started": False,
        "verifier_finished": False,
    }
    isolation: JSONObject = {
        "agent": {}, "verifier": {}, "distinct_images": True,
        "artifact_frozen_after_agent_exit": False, "immutable_handoff": False,
    }
    diagnostics: list[JSONValue] = []
    artifact: bytes | None = None
    artifact_digest: str | None = None

    issues: list[JSONValue] = []
    stage = "environment"
    environment = "failed"
    runner = "unknown"
    termination_kind = "unknown"
    exit_code: int | None = None
    oom_scope = "none"
    verifier_outcome = "not-run"
    verifier_valid = False
    reward: float | None = None
    integrity = "verified"
    pipeline_passed = False
    agent_id: str | None = None
    verifier_id: str | None = None

    def fail(issue: str, diagnostic: str, *, new_stage: str | None = None) -> None:
        nonlocal stage
        if issue not in issues:
            issues.append(issue)
        diagnostics.append(diagnostic)
        if new_stage is not None:
            stage = new_stage

    if doctor.get("ready") is not True:
        fail("runtime-incompatible", "worker-doctor-not-ready")
    else:
        environment = "ready"
        runner = "healthy"
        lifecycle["environment_started"] = True
        agent = _require_object(manifest.get("agent"), "agent")
        verifier = _require_object(manifest.get("verifier"), "verifier")
        agent_image = str(agent["image"])
        verifier_image = str(verifier["image"])
        agent_argv = list(agent["argv"])  # schema validation guarantees strings
        verifier_argv = list(verifier["argv"])
        run_id = str(manifest["run_id"])
        agent_name = f"rolebench-agent-{run_id}"
        verifier_name = f"rolebench-verifier-{run_id}"
        try:
            setup_timeout = _remaining(total_deadline, float(timeouts["setup_seconds"]))
            created = _run(adapter, _create_args(docker, agent_name, agent_image, agent_argv, policy, verifier=False), timeout=setup_timeout)
            if created.returncode != 0:
                code = _creation_issue(created.stderr)
                fail(code, "agent-container-create-failed")
                runner = "failed"
            else:
                agent_id = created.stdout.decode("utf-8", "replace").strip()
                if not agent_id:
                    fail("runner-failure", "agent-container-id-missing")
                    runner = "failed"
                else:
                    inspected = _inspect(adapter, docker, agent_id, _remaining(total_deadline, setup_timeout))
                    agent_facts = _isolation_facts(inspected, policy, verifier=False)
                    isolation["agent"] = agent_facts
                    if not _all_true(agent_facts):
                        fail("sandbox-violation", "agent-effective-isolation-mismatch")
                        runner = "failed"
                    else:
                        lifecycle["agent_started"] = True
                        stage = "agent"
                        artifact_limit = min(int(resources["artifact_bytes_limit"]), int(resources["output_bytes_limit"]))
                        streamed = adapter.stream(
                            [docker, "start", "--attach", agent_id],
                            input_bytes=None,
                            timeout=_remaining(total_deadline, float(timeouts["agent_seconds"])),
                            output_limit=artifact_limit,
                        )
                        if streamed.timed_out:
                            _best_effort(adapter, [docker, "kill", agent_id])
                            fail("runner-failure", "agent-timeout")
                            termination_kind = "orchestrator-timeout"
                        elif streamed.overflowed:
                            _best_effort(adapter, [docker, "kill", agent_id])
                            fail("artifact-collection", "artifact-size-limit-exceeded", new_stage="artifact")
                            termination_kind = "unknown"
                            oom_scope = "none"
                        inspected = _inspect(adapter, docker, agent_id, 30)
                        state_exit, oom, status = _container_state(inspected)
                        exit_code = state_exit
                        lifecycle["agent_finished"] = status in {"exited", "dead"}
                        if oom:
                            fail("runner-failure", "agent-oom")
                            termination_kind = "unknown"
                            oom_scope = "none"
                        elif not streamed.timed_out and not streamed.overflowed and state_exit in {126, 127}:
                            fail("broken-entrypoint", "agent-entrypoint-failed")
                        elif not streamed.timed_out and not streamed.overflowed and (state_exit != 0 or not lifecycle["agent_finished"]):
                            fail("runner-failure", "agent-nonzero-or-not-exited")
                        elif not issues:
                            artifact = streamed.stdout
                            artifact_digest = _sha256(artifact)
                            lifecycle["artifact_frozen"] = True
                            isolation["artifact_frozen_after_agent_exit"] = lifecycle["agent_finished"] is True
                            stage = "artifact"
        except subprocess.TimeoutExpired:
            fail("runner-failure", "setup-timeout")
            termination_kind = "orchestrator-timeout"
            runner = "failed"
        except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
            fail("runner-failure", f"agent-runtime-failure:{type(exc).__name__}")
            runner = "failed"
        finally:
            if agent_id is not None and not lifecycle["artifact_frozen"]:
                _best_effort(adapter, [docker, "kill", agent_id])
            if agent_id is not None:
                _best_effort(adapter, [docker, "rm", "-f", agent_id])

        if artifact is not None and not issues:
            try:
                created = _run(
                    adapter,
                    _create_args(docker, verifier_name, verifier_image, verifier_argv, policy, verifier=True),
                    timeout=_remaining(total_deadline, float(timeouts["setup_seconds"])),
                )
                if created.returncode != 0:
                    fail(_creation_issue(created.stderr), "verifier-container-create-failed", new_stage="verifier")
                else:
                    verifier_id = created.stdout.decode("utf-8", "replace").strip()
                    if not verifier_id:
                        fail("verifier-crash", "verifier-container-id-missing", new_stage="verifier")
                    else:
                        inspected = _inspect(adapter, docker, verifier_id, 30)
                        verifier_facts = _isolation_facts(inspected, policy, verifier=True)
                        isolation["verifier"] = verifier_facts
                        if not _all_true(verifier_facts):
                            fail("sandbox-violation", "verifier-effective-isolation-mismatch", new_stage="verifier")
                        else:
                            lifecycle["verifier_started"] = True
                            stage = "verifier"
                            isolation["immutable_handoff"] = _sha256(artifact) == artifact_digest
                            streamed = adapter.stream(
                                [docker, "start", "--attach", "-i", verifier_id],
                                input_bytes=artifact,
                                timeout=_remaining(total_deadline, float(timeouts["verifier_seconds"])),
                                output_limit=int(resources["output_bytes_limit"]),
                            )
                            if streamed.timed_out:
                                _best_effort(adapter, [docker, "kill", verifier_id])
                                fail("verifier-crash", "verifier-timeout")
                            elif streamed.overflowed:
                                _best_effort(adapter, [docker, "kill", verifier_id])
                                fail("verifier-result-malformed", "verifier-output-size-limit-exceeded")
                            inspected = _inspect(adapter, docker, verifier_id, 30)
                            state_exit, oom, status = _container_state(inspected)
                            lifecycle["verifier_finished"] = status in {"exited", "dead"}
                            if oom or state_exit not in (0,) or streamed.returncode != 0:
                                fail("verifier-crash", "verifier-nonzero-or-oom")
                            elif not lifecycle["verifier_finished"]:
                                fail("verifier-result-missing", "verifier-did-not-exit")
                            elif not issues:
                                parsed = _parse_verifier(streamed.stdout)
                                if parsed is None:
                                    issue = "verifier-result-missing" if not streamed.stdout.strip() else "verifier-result-malformed"
                                    fail(issue, issue)
                                else:
                                    verifier_outcome, reward = parsed
                                    verifier_valid = True
                                    pipeline_passed = True
                                    stage = "complete"
                                    termination_kind = "completed"
            except subprocess.TimeoutExpired:
                fail("verifier-crash", "verifier-setup-timeout", new_stage="verifier")
                termination_kind = "orchestrator-timeout"
            except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
                fail("verifier-crash", f"verifier-runtime-failure:{type(exc).__name__}", new_stage="verifier")
            finally:
                if verifier_id is not None and not lifecycle["verifier_finished"]:
                    _best_effort(adapter, [docker, "kill", verifier_id])
                if verifier_id is not None:
                    _best_effort(adapter, [docker, "rm", "-f", verifier_id])

    if lifecycle["verifier_started"] and not verifier_valid:
        verifier_outcome = "error"
    if termination_kind == "unknown" and issues:
        termination_kind = "completed" if lifecycle["agent_finished"] else "unknown"
    trajectory_facts: JSONObject = {
        "stage": stage,
        "lifecycle": lifecycle,
        "issues": issues,
        "termination_kind": termination_kind,
        "agent_exit_code": exit_code,
        "artifact_digest": artifact_digest,
        "isolation": isolation,
    }
    observation = _observation(
        manifest=manifest,
        policy_digest=policy_digest,
        artifact_digest=artifact_digest,
        lifecycle=lifecycle,
        stage=stage,
        environment=environment,
        runner=runner,
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
        raise WorkerError(f"cannot classify worker observation: {exc}") from exc
    _validate_accounting(root, observation, outcome)
    # A disabled provider cannot produce a valid scored model attempt, even when the
    # local execution and verifier pipeline are healthy.
    if outcome.get("disposition") == "scored":
        raise WorkerError("provider-disabled execution was unexpectedly scored")
    return {
        "schema_version": "omp.worker-run-report/v1",
        "run_id": manifest["run_id"],
        "passed": pipeline_passed and _all_true(_require_object(isolation["agent"], "agent isolation"))
        and _all_true(_require_object(isolation["verifier"], "verifier isolation")),
        "external_provider_calls": 0,
        "policy_digest_sha256": policy_digest,
        "artifact_digest_sha256": artifact_digest,
        "observation": observation,
        "outcome": outcome,
        "doctor": doctor,
        "isolation": isolation,
        "diagnostics": diagnostics,
    }
