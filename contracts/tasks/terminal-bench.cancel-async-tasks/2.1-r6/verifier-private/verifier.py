#!/usr/bin/env python3
"""Strict passive verifier for source-separated cancel-async-tasks evidence."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from typing import Any

RUNNER_EVIDENCE_MAGIC: bytes = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION: str = "omp.runner-evidence/v1"
RUNNER_SNAPSHOT_SCHEMA_VERSION: str = "rolebench.source-separated-runner-snapshot/v1"
VERIFIER_RESULT_SCHEMA_VERSION: str = "omp.verifier-result/v1"
OBSERVATION_AUTHORITY: str = "source-separated-service"
MAX_EVIDENCE_BYTES: int = 32 * 1024 * 1024

HEADER_KEYS = {
    "schema_version",
    "run_id",
    "attempt_nonce",
    "task_digest_sha256",
    "policy_digest_sha256",
    "artifact_digest_sha256",
    "evaluation_request_digest_sha256",
    "verifier_image_digest_sha256",
    "runner",
    "container",
    "stdout",
    "stderr",
}
CONTAINER_KEYS = {
    "container_id",
    "state",
    "exit_code",
    "oom_killed",
    "timed_out",
    "overflowed",
    "duration_seconds",
    "removed",
}
RUNNER_KEYS = {"image", "config_digest_sha256", "platform", "argv"}
STREAM_KEYS = {"byte_count", "digest_sha256", "authority"}
SNAPSHOT_KEYS = {
    "schema_version",
    "status",
    "error",
    "workspace",
    "observation_authority",
    "observer_pid",
    "scenario_count",
    "passed_count",
    "scenarios",
}
COMMON_SCENARIO_KEYS = {
    "scenario_id",
    "passed",
    "observation_authority",
    "observer_pid",
    "candidate_pid",
    "event_digest_sha256",
    "protocol_violations",
}
SCENARIO_KEYS = {
    "empty_tasks": COMMON_SCENARIO_KEYS | {"tasks_executed", "error_type"},
    "invalid_bounds": COMMON_SCENARIO_KEYS
    | {
        "zero_rejected",
        "negative_rejected",
        "string_rejected",
        "bool_rejected",
        "float_rejected",
        "float_integral_rejected",
        "nan_rejected",
        "pos_inf_rejected",
        "neg_inf_rejected",
        "none_rejected",
    },
    "concurrency_bound": COMMON_SCENARIO_KEYS
    | {
        "tasks_started",
        "tasks_finished",
        "tasks_cleaned_up",
        "max_observed_concurrency",
        "concurrency_limit_exceeded",
    },
    "under_limit_concurrency": COMMON_SCENARIO_KEYS
    | {
        "tasks_started",
        "tasks_finished",
        "tasks_cleaned_up",
        "max_observed_concurrency",
    },
    "cancellation_cleanup": COMMON_SCENARIO_KEYS
    | {
        "tasks_started",
        "tasks_finished",
        "tasks_cleaned_up",
        "cleanup_completed_before_return",
        "queued_tasks_prevented",
        "cancellation_propagated",
    },
    "exception_propagation": COMMON_SCENARIO_KEYS
    | {
        "tasks_started",
        "tasks_cleaned_up",
        "error_propagated",
        "all_started_cleaned_up",
        "queued_tasks_prevented",
    },
    "wave_timing": COMMON_SCENARIO_KEYS
    | {
        "timing_valid",
        "total_tasks",
        "max_concurrent_limit",
        "tasks_started",
        "tasks_finished",
        "tasks_cleaned_up",
    },
}
EXPECTED_SCENARIO_ORDER = tuple(SCENARIO_KEYS)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _emit_result(
    *,
    outcome: str,
    reward: int | None,
    run_id: str,
    attempt_nonce: str,
    artifact_digest_sha256: str,
    runner_evidence_digest_sha256: str,
    evaluation_request_digest_sha256: str,
    verifier_image_digest_sha256: str,
) -> int:
    result = {
        "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
        "outcome": outcome,
        "reward": reward,
        "run_id": run_id,
        "attempt_nonce": attempt_nonce,
        "artifact_digest_sha256": artifact_digest_sha256,
        "runner_evidence_digest_sha256": runner_evidence_digest_sha256,
        "evaluation_request_digest_sha256": evaluation_request_digest_sha256,
        "verifier_image_digest_sha256": verifier_image_digest_sha256,
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _load_expected_behavior() -> dict[str, Any] | None:
    verifier_dir = Path(__file__).resolve().parent
    expected_path = verifier_dir / "expected_behavior.json"
    if not expected_path.is_file():
        expected_path = Path("/opt/rolebench/task/verifier-private/expected_behavior.json")
    try:
        value = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _valid_expected_behavior(expected: dict[str, Any]) -> bool:
    scenarios = expected.get("expected_scenarios")
    return (
        set(expected)
        == {
            "schema_version",
            "task_id",
            "runner_snapshot_schema_version",
            "observation_authority",
            "expected_scenario_count",
            "expected_scenarios",
        }
        and expected.get("schema_version")
        == "rolebench.concurrency-expected-behavior/v2"
        and expected.get("task_id") == "terminal-bench.cancel-async-tasks"
        and expected.get("runner_snapshot_schema_version")
        == RUNNER_SNAPSHOT_SCHEMA_VERSION
        and expected.get("observation_authority") == OBSERVATION_AUTHORITY
        and expected.get("expected_scenario_count") == 7
        and isinstance(scenarios, list)
        and scenarios == list(EXPECTED_SCENARIO_ORDER)
    )


def _valid_header(header: object) -> bool:
    if not isinstance(header, dict) or set(header) != HEADER_KEYS:
        return False
    if header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION:
        return False
    if not isinstance(header.get("run_id"), str) or not header.get("run_id"):
        return False
    if any(
        not _is_sha256(header.get(field))
        for field in (
            "attempt_nonce",
            "task_digest_sha256",
            "policy_digest_sha256",
            "artifact_digest_sha256",
            "evaluation_request_digest_sha256",
            "verifier_image_digest_sha256",
        )
    ):
        return False
    container = header.get("container")
    if (
        not isinstance(container, dict)
        or set(container) != CONTAINER_KEYS
        or not isinstance(container.get("container_id"), str)
        or not container.get("container_id")
        or container.get("state") != "exited"
        or container.get("exit_code") != 0
        or container.get("oom_killed") is not False
        or container.get("timed_out") is not False
        or container.get("overflowed") is not False
        or container.get("removed") is not True
        or not isinstance(container.get("duration_seconds"), (int, float))
        or isinstance(container.get("duration_seconds"), bool)
        or not math.isfinite(float(container["duration_seconds"]))
        or float(container["duration_seconds"]) < 0
    ):
        return False
    runner = header.get("runner")
    if (
        not isinstance(runner, dict)
        or set(runner) != RUNNER_KEYS
        or not isinstance(runner.get("image"), str)
        or not runner.get("image")
        or not _is_sha256(runner.get("config_digest_sha256"))
        or not isinstance(runner.get("platform"), dict)
        or not isinstance(runner.get("argv"), list)
        or not all(isinstance(item, str) for item in runner["argv"])
    ):
        return False
    for field in ("stdout", "stderr"):
        stream = header.get(field)
        if (
            not isinstance(stream, dict)
            or set(stream) != STREAM_KEYS
            or not isinstance(stream.get("byte_count"), int)
            or isinstance(stream.get("byte_count"), bool)
            or stream["byte_count"] < 0
            or not _is_sha256(stream.get("digest_sha256"))
            or stream.get("authority") != "untrusted"
        ):
            return False
    return True


def _parse_evidence(raw: bytes) -> tuple[dict[str, Any], bytes, bytes] | None:
    magic_length = len(RUNNER_EVIDENCE_MAGIC)
    if not raw.startswith(RUNNER_EVIDENCE_MAGIC) or len(raw) < magic_length + 8:
        return None
    header_length = struct.unpack(">Q", raw[magic_length : magic_length + 8])[0]
    header_start = magic_length + 8
    header_end = header_start + header_length
    if header_length > MAX_EVIDENCE_BYTES or header_end > len(raw):
        return None
    try:
        header = json.loads(raw[header_start:header_end].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not _valid_header(header):
        return None
    stdout_length = header["stdout"]["byte_count"]
    stderr_length = header["stderr"]["byte_count"]
    if header_end + stdout_length + stderr_length != len(raw):
        return None
    stdout = raw[header_end : header_end + stdout_length]
    stderr = raw[header_end + stdout_length :]
    if (
        hashlib.sha256(stdout).hexdigest() != header["stdout"]["digest_sha256"]
        or hashlib.sha256(stderr).hexdigest() != header["stderr"]["digest_sha256"]
    ):
        return None
    return header, stdout, stderr


def _valid_common_scenario(scenario: dict[str, Any], observer_pid: int) -> bool:
    candidate_pid = scenario.get("candidate_pid")
    return (
        scenario.get("passed") is True
        and scenario.get("observation_authority") == OBSERVATION_AUTHORITY
        and scenario.get("observer_pid") == observer_pid
        and isinstance(candidate_pid, int)
        and not isinstance(candidate_pid, bool)
        and candidate_pid > 0
        and candidate_pid != observer_pid
        and _is_sha256(scenario.get("event_digest_sha256"))
        and scenario.get("protocol_violations") == []
    )


def _valid_scenario(scenario: object, expected_id: str, observer_pid: int) -> bool:
    if (
        not isinstance(scenario, dict)
        or set(scenario) != SCENARIO_KEYS[expected_id]
        or scenario.get("scenario_id") != expected_id
        or not _valid_common_scenario(scenario, observer_pid)
    ):
        return False
    if expected_id == "empty_tasks":
        return scenario.get("tasks_executed") == 0 and scenario.get("error_type") is None
    if expected_id == "invalid_bounds":
        return all(
            scenario.get(field) is True
            for field in (
                "zero_rejected",
                "negative_rejected",
                "string_rejected",
                "bool_rejected",
                "float_rejected",
                "float_integral_rejected",
                "nan_rejected",
                "pos_inf_rejected",
                "neg_inf_rejected",
                "none_rejected",
            )
        )
    if expected_id == "concurrency_bound":
        max_active = scenario.get("max_observed_concurrency")
        return (
            scenario.get("tasks_started") == 8
            and scenario.get("tasks_finished") == 8
            and scenario.get("tasks_cleaned_up") == 8
            and isinstance(max_active, int)
            and not isinstance(max_active, bool)
            and 1 <= max_active <= 3
            and scenario.get("concurrency_limit_exceeded") is False
        )
    if expected_id == "under_limit_concurrency":
        return (
            scenario.get("tasks_started") == 4
            and scenario.get("tasks_finished") == 4
            and scenario.get("tasks_cleaned_up") == 4
            and scenario.get("max_observed_concurrency") == 4
        )
    if expected_id == "cancellation_cleanup":
        return (
            scenario.get("tasks_started") == 3
            and scenario.get("tasks_finished") == 0
            and scenario.get("tasks_cleaned_up") == 3
            and scenario.get("cleanup_completed_before_return") is True
            and scenario.get("queued_tasks_prevented") is True
            and scenario.get("cancellation_propagated") is True
        )
    if expected_id == "exception_propagation":
        return (
            scenario.get("tasks_started") == 2
            and scenario.get("tasks_cleaned_up") == 2
            and scenario.get("error_propagated") is True
            and scenario.get("all_started_cleaned_up") is True
            and scenario.get("queued_tasks_prevented") is True
        )
    if expected_id == "wave_timing":
        return (
            scenario.get("timing_valid") is True
            and scenario.get("total_tasks") == 4
            and scenario.get("max_concurrent_limit") == 2
            and scenario.get("tasks_started") == 4
            and scenario.get("tasks_finished") == 4
            and scenario.get("tasks_cleaned_up") == 4
        )
    return False


def _valid_snapshot(snapshot: object) -> bool:
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_KEYS:
        return False
    observer_pid = snapshot.get("observer_pid")
    if (
        snapshot.get("schema_version") != RUNNER_SNAPSHOT_SCHEMA_VERSION
        or snapshot.get("status") != "applied"
        or snapshot.get("error") is not None
        or snapshot.get("workspace") != "cancel-async-tasks"
        or snapshot.get("observation_authority") != OBSERVATION_AUTHORITY
        or not isinstance(observer_pid, int)
        or isinstance(observer_pid, bool)
        or observer_pid <= 0
        or snapshot.get("scenario_count") != 7
        or snapshot.get("passed_count") != 7
    ):
        return False
    scenarios = snapshot.get("scenarios")
    return (
        isinstance(scenarios, list)
        and len(scenarios) == 7
        and all(
            _valid_scenario(scenario, expected_id, observer_pid)
            for scenario, expected_id in zip(scenarios, EXPECTED_SCENARIO_ORDER, strict=True)
        )
    )


def main() -> int:
    raw_evidence = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    evidence_digest = hashlib.sha256(raw_evidence).hexdigest()
    fallback = {
        "run_id": "unknown",
        "attempt_nonce": "0" * 64,
        "artifact_digest_sha256": "0" * 64,
        "evaluation_request_digest_sha256": "0" * 64,
        "verifier_image_digest_sha256": "0" * 64,
    }
    if not raw_evidence or len(raw_evidence) > MAX_EVIDENCE_BYTES:
        return _emit_result(
            outcome="error",
            reward=None,
            runner_evidence_digest_sha256=evidence_digest,
            **fallback,
        )
    parsed = _parse_evidence(raw_evidence)
    if parsed is None:
        return _emit_result(
            outcome="error",
            reward=None,
            runner_evidence_digest_sha256=evidence_digest,
            **fallback,
        )
    header, stdout, stderr = parsed
    identity = {
        "run_id": str(header["run_id"]),
        "attempt_nonce": str(header["attempt_nonce"]),
        "artifact_digest_sha256": str(header["artifact_digest_sha256"]),
        "evaluation_request_digest_sha256": str(
            header["evaluation_request_digest_sha256"]
        ),
        "verifier_image_digest_sha256": str(header["verifier_image_digest_sha256"]),
    }

    def verdict(outcome: str, reward: int) -> int:
        return _emit_result(
            outcome=outcome,
            reward=reward,
            runner_evidence_digest_sha256=evidence_digest,
            **identity,
        )

    if stderr:
        return verdict("rejected", 0)
    expected = _load_expected_behavior()
    if expected is None or not _valid_expected_behavior(expected):
        return _emit_result(
            outcome="error",
            reward=None,
            runner_evidence_digest_sha256=evidence_digest,
            **identity,
        )
    try:
        snapshot = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return verdict("rejected", 0)
    if isinstance(snapshot, dict):
        status = snapshot.get("status")
        if status in ("harness-error", "harness-timeout") or snapshot.get("error") == "harness-error":
            return _emit_result(
                outcome="error",
                reward=None,
                runner_evidence_digest_sha256=evidence_digest,
                **identity,
            )
    if not _valid_snapshot(snapshot):
        return verdict("rejected", 0)
    return verdict("accepted", 1)


if __name__ == "__main__":
    raise SystemExit(main())
