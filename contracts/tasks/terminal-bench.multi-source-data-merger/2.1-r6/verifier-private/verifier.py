#!/usr/bin/env python3
"""Passive exact-result verifier for the multi-source merger task."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from typing import NoReturn

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
HEADER_KEYS = {
    "schema_version",
    "attempt_nonce",
    "run_id",
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
IO_KEYS = {"byte_count", "digest_sha256", "authority"}
SNAPSHOT_KEYS = {"schema_version", "status", "error", "submission"}


class EvidenceError(ValueError):
    """Malformed runner evidence or result payload."""


def _reject_constant(value: str) -> NoReturn:
    raise EvidenceError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


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
        "run_id": run_id,
        "attempt_nonce": attempt_nonce,
        "outcome": outcome,
        "reward": reward,
        "artifact_digest_sha256": artifact_digest_sha256,
        "runner_evidence_digest_sha256": runner_evidence_digest_sha256,
        "evaluation_request_digest_sha256": evaluation_request_digest_sha256,
        "verifier_image_digest_sha256": verifier_image_digest_sha256,
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    evidence_digest = hashlib.sha256(raw).hexdigest()
    fallback = {
        "run_id": "unknown",
        "attempt_nonce": "0" * 64,
        "artifact_digest_sha256": "0" * 64,
        "evaluation_request_digest_sha256": "0" * 64,
        "verifier_image_digest_sha256": "0" * 64,
    }

    def emit(outcome: str, reward: int | None, values: dict[str, str] = fallback) -> int:
        return _emit_result(
            outcome=outcome,
            reward=reward,
            run_id=values["run_id"],
            attempt_nonce=values["attempt_nonce"],
            artifact_digest_sha256=values["artifact_digest_sha256"],
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256=values["evaluation_request_digest_sha256"],
            verifier_image_digest_sha256=values["verifier_image_digest_sha256"],
        )

    if not raw or len(raw) > MAX_EVIDENCE_BYTES:
        return emit("error", None)
    magic_len = len(RUNNER_EVIDENCE_MAGIC)
    if not raw.startswith(RUNNER_EVIDENCE_MAGIC) or len(raw) < magic_len + 8:
        return emit("error", None)
    header_length = struct.unpack(">Q", raw[magic_len : magic_len + 8])[0]
    header_start = magic_len + 8
    header_end = header_start + header_length
    if header_end > len(raw):
        return emit("error", None)
    header_bytes = raw[header_start:header_end]
    try:
        header = json.loads(
            header_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return emit("error", None)
    if (
        not isinstance(header, dict)
        or set(header) != HEADER_KEYS
        or header_bytes != json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        or header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION
        or not isinstance(header.get("run_id"), str)
        or not header.get("run_id")
        or any(
            not _is_sha256(header.get(field))
            for field in (
                "attempt_nonce",
                "task_digest_sha256",
                "policy_digest_sha256",
                "artifact_digest_sha256",
                "evaluation_request_digest_sha256",
                "verifier_image_digest_sha256",
            )
        )
    ):
        return emit("error", None)
    bound = {
        "run_id": str(header["run_id"]),
        "attempt_nonce": str(header["attempt_nonce"]),
        "artifact_digest_sha256": str(header["artifact_digest_sha256"]),
        "evaluation_request_digest_sha256": str(header["evaluation_request_digest_sha256"]),
        "verifier_image_digest_sha256": str(header["verifier_image_digest_sha256"]),
    }

    def reject() -> int:
        return emit("rejected", 0, bound)

    container = header.get("container")
    duration = container.get("duration_seconds") if isinstance(container, dict) else None
    if (
        not isinstance(container, dict)
        or set(container) != CONTAINER_KEYS
        or container.get("state") != "exited"
        or container.get("exit_code") != 0
        or container.get("oom_killed") is not False
        or container.get("timed_out") is not False
        or container.get("overflowed") is not False
        or container.get("removed") is not True
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        return reject()
    stdout_info = header.get("stdout")
    stderr_info = header.get("stderr")
    if (
        not isinstance(stdout_info, dict)
        or not isinstance(stderr_info, dict)
        or set(stdout_info) != IO_KEYS
        or set(stderr_info) != IO_KEYS
        or stdout_info.get("authority") != "untrusted"
        or stderr_info.get("authority") != "untrusted"
    ):
        return reject()
    stdout_len = stdout_info.get("byte_count")
    stderr_len = stderr_info.get("byte_count")
    if (
        isinstance(stdout_len, bool)
        or isinstance(stderr_len, bool)
        or not isinstance(stdout_len, int)
        or not isinstance(stderr_len, int)
        or stdout_len < 0
        or stderr_len < 0
    ):
        return reject()
    stdout_start = header_end
    stdout_end = stdout_start + stdout_len
    stderr_end = stdout_end + stderr_len
    if stderr_end != len(raw):
        return reject()
    stdout_bytes = raw[stdout_start:stdout_end]
    stderr_bytes = raw[stdout_end:stderr_end]
    if (
        hashlib.sha256(stdout_bytes).hexdigest() != stdout_info.get("digest_sha256")
        or hashlib.sha256(stderr_bytes).hexdigest() != stderr_info.get("digest_sha256")
        or stderr_bytes
    ):
        return reject()
    try:
        snapshot = json.loads(
            stdout_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return reject()
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != SNAPSHOT_KEYS
        or stdout_bytes != json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        or snapshot.get("schema_version") != "rolebench.multi-source-runner-snapshot/v1"
        or snapshot.get("status") != "parsed"
        or snapshot.get("error") is not None
    ):
        return reject()
    expected_path = Path(__file__).resolve().parent / "expected_submission.json"
    if not expected_path.is_file():
        expected_path = Path("/opt/rolebench/task/verifier-private/expected_submission.json")
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return reject()
    if snapshot.get("submission") != expected:
        return reject()
    return emit("accepted", 1, bound)


if __name__ == "__main__":
    raise SystemExit(main())
