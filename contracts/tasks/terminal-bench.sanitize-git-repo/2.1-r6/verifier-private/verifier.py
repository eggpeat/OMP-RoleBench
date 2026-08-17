#!/usr/bin/env python3
"""Strict passive verifier for sanitize-git-repo runner evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

RUNNER_EVIDENCE_MAGIC: bytes = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION: str = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION: str = "omp.verifier-result/v1"
MAX_EVIDENCE_BYTES: int = 32 * 1024 * 1024

KNOWN_SECRETS: tuple[str, ...] = (
    "akiaexample000000000",
    "examplesecretkey000000000000000000000000",
    "ghp_exampleexampleexampleexampleexample0",
    "hf_exampleexampleexampleexample0",
    "hf_exampleexampleexampleexample1",
)

REQUIRED_PLACEHOLDERS: tuple[str, ...] = (
    "<your-aws-access-key-id>",
    "<your-aws-secret-access-key>",
    "<your-github-token>",
    "<your-huggingface-token>",
)

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
SNAPSHOT_KEYS = {"schema_version", "status", "error", "workspace", "files"}
FILE_KEYS = {"path", "mode", "size_bytes", "sha256", "content_base64"}


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
    raw_evidence = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    if not raw_evidence or len(raw_evidence) > MAX_EVIDENCE_BYTES:
        return _emit_result(
            outcome="error",
            reward=None,
            run_id="unknown",
            attempt_nonce="0" * 64,
            artifact_digest_sha256="0" * 64,
            runner_evidence_digest_sha256=hashlib.sha256(raw_evidence).hexdigest(),
            evaluation_request_digest_sha256="0" * 64,
            verifier_image_digest_sha256="0" * 64,
        )

    evidence_digest = hashlib.sha256(raw_evidence).hexdigest()
    magic_len = len(RUNNER_EVIDENCE_MAGIC)

    if not raw_evidence.startswith(RUNNER_EVIDENCE_MAGIC) or len(raw_evidence) < magic_len + 8:
        return _emit_result(
            outcome="error",
            reward=None,
            run_id="unknown",
            attempt_nonce="0" * 64,
            artifact_digest_sha256="0" * 64,
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256="0" * 64,
            verifier_image_digest_sha256="0" * 64,
        )

    header_len = struct.unpack(">Q", raw_evidence[magic_len : magic_len + 8])[0]
    header_start = magic_len + 8
    header_end = header_start + header_len

    if len(raw_evidence) < header_end:
        return _emit_result(
            outcome="error",
            reward=None,
            run_id="unknown",
            attempt_nonce="0" * 64,
            artifact_digest_sha256="0" * 64,
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256="0" * 64,
            verifier_image_digest_sha256="0" * 64,
        )

    header_bytes = raw_evidence[header_start:header_end]
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception:
        return _emit_result(
            outcome="error",
            reward=None,
            run_id="unknown",
            attempt_nonce="0" * 64,
            artifact_digest_sha256="0" * 64,
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256="0" * 64,
            verifier_image_digest_sha256="0" * 64,
        )

    if (
        set(header) != HEADER_KEYS
        or header_bytes
        != json.dumps(
            header,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        or not _is_sha256(header.get("attempt_nonce"))
        or not _is_sha256(header.get("task_digest_sha256"))
        or not _is_sha256(header.get("policy_digest_sha256"))
        or not _is_sha256(header.get("artifact_digest_sha256"))
        or not _is_sha256(header.get("evaluation_request_digest_sha256"))
        or not _is_sha256(header.get("verifier_image_digest_sha256"))
    ):
        return _emit_result(
            outcome="error",
            reward=None,
            run_id="unknown",
            attempt_nonce="0" * 64,
            artifact_digest_sha256="0" * 64,
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256="0" * 64,
            verifier_image_digest_sha256="0" * 64,
        )

    run_id = str(header.get("run_id", "unknown"))
    attempt_nonce = str(header.get("attempt_nonce", "0" * 64))
    artifact_digest = str(header.get("artifact_digest_sha256", "0" * 64))
    eval_req_digest = str(header.get("evaluation_request_digest_sha256", "0" * 64))
    verifier_image_digest = str(header.get("verifier_image_digest_sha256", "0" * 64))

    def reject() -> int:
        return _emit_result(
            outcome="rejected",
            reward=0,
            run_id=run_id,
            attempt_nonce=attempt_nonce,
            artifact_digest_sha256=artifact_digest,
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256=eval_req_digest,
            verifier_image_digest_sha256=verifier_image_digest,
        )

    def accept() -> int:
        return _emit_result(
            outcome="accepted",
            reward=1,
            run_id=run_id,
            attempt_nonce=attempt_nonce,
            artifact_digest_sha256=artifact_digest,
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256=eval_req_digest,
            verifier_image_digest_sha256=verifier_image_digest,
        )

    # Validate header schema version and container state
    if header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION:
        return reject()

    container = header.get("container")
    if not isinstance(container, dict) or set(container) != CONTAINER_KEYS:
        return reject()
    duration = container.get("duration_seconds")
    if (
        container.get("state") != "exited"
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

    if len(raw_evidence) != stderr_end:
        return reject()

    stdout_bytes = raw_evidence[stdout_start:stdout_end]
    stderr_bytes = raw_evidence[stdout_end:stderr_end]

    if hashlib.sha256(stdout_bytes).hexdigest() != stdout_info.get("digest_sha256"):
        return reject()
    if hashlib.sha256(stderr_bytes).hexdigest() != stderr_info.get("digest_sha256"):
        return reject()

    # Parse untrusted runner stdout
    try:
        snapshot = json.loads(stdout_bytes.decode("utf-8"))
    except Exception:
        return reject()

    if not isinstance(snapshot, dict):
        return reject()
    if (
        set(snapshot) != SNAPSHOT_KEYS
        or snapshot.get("workspace") != "dclm"
    ):
        return reject()

    if snapshot.get("schema_version") != "rolebench.runner-snapshot/v1":
        return reject()
    if snapshot.get("status") != "applied" or snapshot.get("error") is not None:
        return reject()

    snapshot_files = snapshot.get("files")
    if not isinstance(snapshot_files, list):
        return reject()

    # Load private expected state
    script_dir = Path(__file__).resolve().parent
    expected_path = script_dir / "expected_state.json"
    if not expected_path.is_file():
        expected_path = Path("/opt/rolebench/task/verifier-private/expected_state.json")
    if not expected_path.is_file():
        return reject()

    try:
        expected_state = json.loads(expected_path.read_text(encoding="utf-8"))
    except Exception:
        return reject()

    if not isinstance(expected_state, dict):
        return reject()

    if len(snapshot_files) != len(expected_state):
        return reject()

    # Verify each snapshot file against private ground truth
    found_paths: set[str] = set()
    observed_texts: list[str] = []
    for item in snapshot_files:
        if not isinstance(item, dict):
            return reject()
        if set(item) != FILE_KEYS:
            return reject()
        rel_path = item.get("path")
        if not isinstance(rel_path, str) or rel_path not in expected_state:
            return reject()
        found_paths.add(rel_path)

        expected_item = expected_state[rel_path]
        b64_content = item.get("content_base64")
        if not isinstance(b64_content, str):
            return reject()

        try:
            file_bytes = base64.b64decode(b64_content.encode("ascii"), validate=True)
        except Exception:
            return reject()

        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        if file_sha256 != item.get("sha256"):
            return reject()
        if item.get("size_bytes") != len(file_bytes):
            return reject()
        if item.get("mode") != expected_item.get("mode"):
            return reject()

        # For files that should be untouched (clean sentinels), verify exact match
        if expected_item.get("clean", False):
            if file_sha256 != expected_item.get("sha256"):
                return reject()
            if item.get("size_bytes") != expected_item.get("size_bytes"):
                return reject()
            if b64_content != expected_item.get("content_base64"):
                return reject()

        try:
            file_text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return reject()
        observed_texts.append(file_text)

        # Check that no known secret exists in the file text
        text_lower = file_text.lower()
        for secret in KNOWN_SECRETS:
            if secret in text_lower:
                return reject()

    if found_paths != set(expected_state.keys()):
        return reject()
    combined_text = "\n".join(observed_texts)
    if any(
        placeholder not in combined_text
        for placeholder in REQUIRED_PLACEHOLDERS
    ):
        return reject()

    return accept()


if __name__ == "__main__":
    raise SystemExit(main())
