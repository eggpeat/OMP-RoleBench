#!/usr/bin/env python3
"""Passive host evidence verifier for docker-runsc fixture."""

import hashlib
import json
import struct
import sys

_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
_MAX_BYTES = 16 * 1024 * 1024


def main() -> None:
    raw = sys.stdin.buffer.read(_MAX_BYTES)
    if not raw or not raw.startswith(_MAGIC) or len(raw) < len(_MAGIC) + 8:
        _emit_error("missing or invalid runner evidence")
        return

    header_len = struct.unpack(">Q", raw[len(_MAGIC) : len(_MAGIC) + 8])[0]
    if len(raw) < len(_MAGIC) + 8 + header_len:
        _emit_error("truncated runner evidence header")
        return

    try:
        header_bytes = raw[len(_MAGIC) + 8 : len(_MAGIC) + 8 + header_len]
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:
        _emit_error(f"cannot parse evidence header: {exc}")
        return

    stdout_start = len(_MAGIC) + 8 + header_len
    try:
        stdout_len = int(header["stdout"]["byte_count"])
        stderr_len = int(header["stderr"]["byte_count"])
    except (KeyError, TypeError, ValueError) as exc:
        _emit_error(f"malformed stream metadata: {exc}")
        return

    if len(raw) < stdout_start + stdout_len + stderr_len:
        _emit_error("truncated runner evidence body")
        return

    stdout_bytes = raw[stdout_start : stdout_start + stdout_len]
    stderr_bytes = raw[stdout_start + stdout_len : stdout_start + stdout_len + stderr_len]

    if hashlib.sha256(stdout_bytes).hexdigest() != header.get("stdout", {}).get("digest_sha256"):
        _emit_error("stdout digest mismatch")
        return
    if hashlib.sha256(stderr_bytes).hexdigest() != header.get("stderr", {}).get("digest_sha256"):
        _emit_error("stderr digest mismatch")
        return

    evidence_sha = hashlib.sha256(raw).hexdigest()
    exit_code = header.get("container", {}).get("exit_code")

    if stdout_bytes == b"rolebench-docker-runsc-runner-pass" and exit_code == 0:
        outcome = "accepted"
        reward = 1.0
    else:
        outcome = "rejected"
        reward = 0.0

    result = {
        "schema_version": "omp.verifier-result/v1",
        "run_id": str(header.get("run_id", "unknown")),
        "attempt_nonce": str(header.get("attempt_nonce", "0" * 64)),
        "outcome": outcome,
        "reward": reward,
        "artifact_digest_sha256": str(header.get("artifact_digest_sha256", "0" * 64)),
        "runner_evidence_digest_sha256": evidence_sha,
        "evaluation_request_digest_sha256": str(header.get("evaluation_request_digest_sha256", "0" * 64)),
        "verifier_image_digest_sha256": str(header.get("verifier_image_digest_sha256", "0" * 64)),
    }
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")


def _emit_error(message: str) -> None:
    sys.stderr.write(f"rolebench-verifier: {message}\n")
    error_result = {
        "schema_version": "omp.verifier-result/v1",
        "run_id": "unknown",
        "attempt_nonce": "0" * 64,
        "outcome": "error",
        "reward": None,
        "artifact_digest_sha256": "0" * 64,
        "runner_evidence_digest_sha256": "0" * 64,
        "evaluation_request_digest_sha256": "0" * 64,
        "verifier_image_digest_sha256": "0" * 64,
    }
    sys.stdout.write(json.dumps(error_result, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
