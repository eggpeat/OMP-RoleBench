"""Wire format, evaluation request hashing, and strict verdict verification for RoleBench v1."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Any

from .contracts import JSONObject, canonical_json, canonical_sha256

RUNNER_EVIDENCE_MAGIC: bytes = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION: str = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION: str = "omp.verifier-result/v1"
EVALUATION_REQUEST_SCHEMA_VERSION: str = "omp.evaluation-request/v1"

_VERIFIER_RESULT_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "attempt_nonce",
        "outcome",
        "reward",
        "artifact_digest_sha256",
        "runner_evidence_digest_sha256",
        "evaluation_request_digest_sha256",
        "verifier_image_digest_sha256",
    }
)


class ProtocolError(ValueError):
    """A runner evidence payload or verifier result violates the wire contract."""


def compute_evaluation_request_digest(
    *,
    attempt_nonce: str,
    run_id: str,
    task_digest_sha256: str,
    runner_manifest: JSONObject,
    verifier_manifest: JSONObject,
) -> str:
    """Return the canonical SHA-256 digest of the bound evaluation request."""
    request: JSONObject = {
        "schema_version": EVALUATION_REQUEST_SCHEMA_VERSION,
        "attempt_nonce": attempt_nonce,
        "run_id": run_id,
        "task_digest_sha256": task_digest_sha256,
        "runner": runner_manifest,
        "verifier": verifier_manifest,
    }
    return canonical_sha256(request)


def build_runner_evidence(
    *,
    attempt_nonce: str,
    run_id: str,
    task_digest_sha256: str,
    policy_digest_sha256: str,
    artifact_digest_sha256: str,
    evaluation_request_digest_sha256: str,
    verifier_image_digest_sha256: str,
    runner_manifest: JSONObject,
    container_id: str,
    state: str,
    exit_code: int | None,
    oom_killed: bool,
    timed_out: bool,
    overflowed: bool,
    duration_seconds: float,
    removed: bool,
    stdout: bytes,
    stderr: bytes,
) -> bytes:
    """Build the canonical, sealed wire format for host-authored runner evidence."""
    stdout_digest = hashlib.sha256(stdout).hexdigest()
    stderr_digest = hashlib.sha256(stderr).hexdigest()
    header: JSONObject = {
        "schema_version": RUNNER_EVIDENCE_SCHEMA_VERSION,
        "attempt_nonce": attempt_nonce,
        "run_id": run_id,
        "task_digest_sha256": task_digest_sha256,
        "policy_digest_sha256": policy_digest_sha256,
        "artifact_digest_sha256": artifact_digest_sha256,
        "evaluation_request_digest_sha256": evaluation_request_digest_sha256,
        "verifier_image_digest_sha256": verifier_image_digest_sha256,
        "runner": {
            "image": str(runner_manifest.get("image", "")),
            "config_digest_sha256": runner_manifest.get("config_digest_sha256"),
            "platform": runner_manifest.get("platform"),
            "argv": list(runner_manifest.get("argv", [])),
        },
        "container": {
            "container_id": container_id,
            "state": state,
            "exit_code": exit_code,
            "oom_killed": oom_killed,
            "timed_out": timed_out,
            "overflowed": overflowed,
            "duration_seconds": duration_seconds,
            "removed": removed,
        },
        "stdout": {
            "byte_count": len(stdout),
            "digest_sha256": stdout_digest,
            "authority": "untrusted",
        },
        "stderr": {
            "byte_count": len(stderr),
            "digest_sha256": stderr_digest,
            "authority": "untrusted",
        },
    }
    header_bytes = canonical_json(header).encode("utf-8")
    header_len = struct.pack(">Q", len(header_bytes))
    return RUNNER_EVIDENCE_MAGIC + header_len + header_bytes + stdout + stderr


def parse_runner_evidence(evidence_bytes: bytes) -> tuple[JSONObject, bytes, bytes]:
    """Parse and validate sealed runner evidence bytes into header, stdout, and stderr."""
    magic_len = len(RUNNER_EVIDENCE_MAGIC)
    if not evidence_bytes.startswith(RUNNER_EVIDENCE_MAGIC):
        raise ProtocolError("invalid runner evidence magic")
    if len(evidence_bytes) < magic_len + 8:
        raise ProtocolError("runner evidence truncated before header length")
    header_len = struct.unpack(">Q", evidence_bytes[magic_len : magic_len + 8])[0]
    header_start = magic_len + 8
    header_end = header_start + header_len
    if len(evidence_bytes) < header_end:
        raise ProtocolError("runner evidence truncated before header end")
    header_bytes = evidence_bytes[header_start:header_end]
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"runner evidence header is not valid JSON: {exc}") from exc
    if not isinstance(header, dict):
        raise ProtocolError("runner evidence header must be a JSON object")
    if header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION:
        raise ProtocolError("unsupported runner evidence schema version")

    stdout_info = header.get("stdout")
    stderr_info = header.get("stderr")
    if not isinstance(stdout_info, dict) or not isinstance(stderr_info, dict):
        raise ProtocolError("runner evidence stdout/stderr info missing or invalid")
    stdout_len = stdout_info.get("byte_count")
    stderr_len = stderr_info.get("byte_count")
    if not isinstance(stdout_len, int) or not isinstance(stderr_len, int):
        raise ProtocolError("stdout/stderr byte_count must be integer")
    if stdout_len < 0 or stderr_len < 0:
        raise ProtocolError("stdout/stderr byte_count cannot be negative")

    stdout_start = header_end
    stdout_end = stdout_start + stdout_len
    stderr_end = stdout_end + stderr_len
    if len(evidence_bytes) != stderr_end:
        raise ProtocolError(
            f"runner evidence length mismatch: expected {stderr_end} bytes, got {len(evidence_bytes)}"
        )
    stdout = evidence_bytes[stdout_start:stdout_end]
    stderr = evidence_bytes[stdout_end:stderr_end]
    if hashlib.sha256(stdout).hexdigest() != stdout_info.get("digest_sha256"):
        raise ProtocolError("runner evidence stdout SHA-256 digest mismatch")
    if hashlib.sha256(stderr).hexdigest() != stderr_info.get("digest_sha256"):
        raise ProtocolError("runner evidence stderr SHA-256 digest mismatch")
    return header, stdout, stderr


def parse_verifier_result(raw_stdout: bytes) -> JSONObject:
    """Parse raw verifier stdout as a single JSON object without trailing data."""
    try:
        text = raw_stdout.decode("utf-8")
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(text)
        if text[end:].strip():
            raise ProtocolError("trailing data after verifier result JSON")
        if not isinstance(value, dict):
            raise ProtocolError("verifier result must be a JSON object")
        return value
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot parse verifier result: {exc}") from exc


def validate_verifier_result(
    result: JSONObject,
    *,
    expected_run_id: str,
    expected_attempt_nonce: str,
    expected_artifact_digest_sha256: str,
    expected_runner_evidence_digest_sha256: str,
    expected_evaluation_request_digest_sha256: str,
    expected_verifier_image_digest_sha256: str,
) -> tuple[str, float | None]:
    """Validate strict verifier result bindings.

    Returns (outcome, reward) where outcome is 'accepted', 'rejected', or 'error'.
    """
    if set(result.keys()) != _VERIFIER_RESULT_REQUIRED_KEYS:
        raise ProtocolError(
            f"verifier result keys mismatch: expected {sorted(_VERIFIER_RESULT_REQUIRED_KEYS)}, got {sorted(result.keys())}"
        )
    if result.get("schema_version") != VERIFIER_RESULT_SCHEMA_VERSION:
        raise ProtocolError("unsupported verifier result schema version")
    if result.get("run_id") != expected_run_id:
        raise ProtocolError(
            f"run_id mismatch: expected {expected_run_id!r}, got {result.get('run_id')!r}"
        )
    if result.get("attempt_nonce") != expected_attempt_nonce:
        raise ProtocolError(
            f"attempt_nonce mismatch: expected {expected_attempt_nonce!r}, got {result.get('attempt_nonce')!r}"
        )
    if result.get("artifact_digest_sha256") != expected_artifact_digest_sha256:
        raise ProtocolError(
            f"artifact_digest_sha256 mismatch: expected {expected_artifact_digest_sha256!r}, got {result.get('artifact_digest_sha256')!r}"
        )
    if (
        result.get("runner_evidence_digest_sha256")
        != expected_runner_evidence_digest_sha256
    ):
        raise ProtocolError(
            f"runner_evidence_digest_sha256 mismatch: expected {expected_runner_evidence_digest_sha256!r}, got {result.get('runner_evidence_digest_sha256')!r}"
        )
    if (
        result.get("evaluation_request_digest_sha256")
        != expected_evaluation_request_digest_sha256
    ):
        raise ProtocolError(
            f"evaluation_request_digest_sha256 mismatch: expected {expected_evaluation_request_digest_sha256!r}, got {result.get('evaluation_request_digest_sha256')!r}"
        )
    if (
        result.get("verifier_image_digest_sha256")
        != expected_verifier_image_digest_sha256
    ):
        raise ProtocolError(
            f"verifier_image_digest_sha256 mismatch: expected {expected_verifier_image_digest_sha256!r}, got {result.get('verifier_image_digest_sha256')!r}"
        )

    outcome = result.get("outcome")
    reward = result.get("reward")
    if outcome not in {"accepted", "rejected", "error"}:
        raise ProtocolError(f"invalid verifier outcome: {outcome!r}")
    if outcome == "error":
        if reward is not None:
            raise ProtocolError("error outcome requires null reward")
        return "error", None
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        raise ProtocolError(f"invalid verifier reward: {reward!r}")
    if outcome == "accepted" and reward != 1:
        raise ProtocolError(f"accepted outcome requires reward == 1, got {reward!r}")
    if outcome == "rejected" and not (0 <= reward < 1):
        raise ProtocolError(f"rejected outcome requires 0 <= reward < 1, got {reward!r}")
    return outcome, float(reward)
