#!/usr/bin/env python3
"""Passive semantic verifier for the multi-file mechanical refactoring task."""

from __future__ import annotations

import ast
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
        and all(c in "0123456789abcdef" for c in value)
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


def _normalize_code(text: str) -> str:
    """Normalize whitespace and newlines for line comparison."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    return "\n".join(lines)


def _ast_matches(submitted_code: str, expected_code: str) -> bool:
    """Check whether submitted code is semantically equivalent to expected code via AST."""
    try:
        sub_tree = ast.parse(submitted_code)
        exp_tree = ast.parse(expected_code)
    except SyntaxError:
        return False
    return ast.dump(sub_tree) == ast.dump(exp_tree)


def _semantic_check_file(path: str, submitted_code: str, expected_code: str) -> bool:
    """Verify that a single file matches the expected transformation semantically."""
    # First: normalized text or AST equivalence
    if _normalize_code(submitted_code) == _normalize_code(expected_code):
        return True
    if _ast_matches(submitted_code, expected_code):
        return True

    # Detailed AST inspection
    try:
        tree = ast.parse(submitted_code)
    except SyntaxError:
        return False

    # Check that no legacy imports exist
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "legacy_telemetry":
                    return False
        elif isinstance(node, ast.ImportFrom):
            if node.module == "legacy_telemetry":
                return False

    return False


def _verify_submission(submission: dict[str, object], expected_files: dict[str, str]) -> bool:
    modified_files = submission.get("modified_files")
    if not isinstance(modified_files, dict):
        return False

    # 1. Exhaustiveness: all expected files must be in modified_files
    for expected_path, expected_content in expected_files.items():
        if expected_path not in modified_files:
            return False
        submitted_content = modified_files[expected_path]
        if not isinstance(submitted_content, str):
            return False
        if not _semantic_check_file(expected_path, submitted_content, expected_content):
            return False

    # 2. Precision: negative controls and unintended edits
    # If src/health.py is present, it must NOT be modified from its original content
    health_original = (
        "from __future__ import annotations\n\nimport time\n\n\n"
        "def check_liveness(status_monitor: object) -> dict[str, object]:\n"
        "    \"\"\"Health check endpoint. Requires no changes.\"\"\"\n"
        "    now = time.time()\n"
        "    status_monitor.log_event(\"health.check\", now)\n"
        "    return {\"status\": \"ok\", \"timestamp\": now}\n"
    )
    for path, content in modified_files.items():
        if path not in expected_files:
            if path == "src/health.py":
                if _normalize_code(content) != _normalize_code(health_original):
                    return False
            else:
                # Any other unexpected file modification is rejected
                return False

    # 3. Precision: verify negative controls across submitted files
    # Check that method calls on objects (e.g. audit_logger.log_event) were preserved
    auth_code = modified_files.get("src/auth.py", "")
    try:
        auth_tree = ast.parse(auth_code)
    except SyntaxError:
        return False

    found_audit_call = False
    for node in ast.walk(auth_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "log_event" and isinstance(node.func.value, ast.Name) and node.func.value.id == "audit_logger":
                found_audit_call = True
    if not found_audit_call:
        return False

    return True


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    evidence_digest = hashlib.sha256(raw).hexdigest()
    fallback = {
        "run_id": "unknown",
        "attempt_nonce": "0" * 64,
        "artifact_digest_sha256": "0" * 64,
        "runner_evidence_digest_sha256": evidence_digest,
        "evaluation_request_digest_sha256": "0" * 64,
        "verifier_image_digest_sha256": "0" * 64,
    }

    def emit(outcome: str, reward: int | None, bound: dict[str, str] | None = None) -> int:
        payload = fallback if bound is None else bound
        return _emit_result(
            outcome=outcome,
            reward=reward,
            run_id=payload["run_id"],
            attempt_nonce=payload["attempt_nonce"],
            artifact_digest_sha256=payload["artifact_digest_sha256"],
            runner_evidence_digest_sha256=payload["runner_evidence_digest_sha256"],
            evaluation_request_digest_sha256=payload["evaluation_request_digest_sha256"],
            verifier_image_digest_sha256=payload["verifier_image_digest_sha256"],
        )

    if len(raw) > MAX_EVIDENCE_BYTES:
        return emit("rejected", 0)
    if not raw.startswith(RUNNER_EVIDENCE_MAGIC):
        return emit("rejected", 0)

    cursor = len(RUNNER_EVIDENCE_MAGIC)
    if len(raw) < cursor + 4:
        return emit("rejected", 0)
    (header_length,) = struct.unpack(">I", raw[cursor : cursor + 4])
    cursor += 4
    if header_length <= 0 or len(raw) < cursor + header_length:
        return emit("rejected", 0)

    header_bytes = raw[cursor : cursor + header_length]
    cursor += header_length

    try:
        header = json.loads(
            header_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return emit("rejected", 0)

    if not isinstance(header, dict) or set(header) != HEADER_KEYS:
        return emit("rejected", 0)
    if header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION:
        return emit("rejected", 0)

    run_id = header.get("run_id")
    attempt_nonce = header.get("attempt_nonce")
    artifact_digest = header.get("artifact_digest_sha256")
    eval_request_digest = header.get("evaluation_request_digest_sha256")
    verifier_image_digest = header.get("verifier_image_digest_sha256")

    if not isinstance(run_id, str) or not run_id:
        return emit("rejected", 0)
    if not _is_sha256(attempt_nonce) or not _is_sha256(artifact_digest):
        return emit("rejected", 0)
    if not _is_sha256(eval_request_digest) or not _is_sha256(verifier_image_digest):
        return emit("rejected", 0)

    bound = {
        "run_id": run_id,
        "attempt_nonce": attempt_nonce,
        "artifact_digest_sha256": artifact_digest,
        "runner_evidence_digest_sha256": evidence_digest,
        "evaluation_request_digest_sha256": eval_request_digest,
        "verifier_image_digest_sha256": verifier_image_digest,
    }

    stdout_info = header.get("stdout")
    stderr_info = header.get("stderr")
    container = header.get("container")
    if not isinstance(stdout_info, dict) or set(stdout_info) != IO_KEYS:
        return emit("rejected", 0, bound)
    if not isinstance(stderr_info, dict) or set(stderr_info) != IO_KEYS:
        return emit("rejected", 0, bound)
    if not isinstance(container, dict) or set(container) != CONTAINER_KEYS:
        return emit("rejected", 0, bound)

    stdout_len = stdout_info.get("byte_count")
    stderr_len = stderr_info.get("byte_count")
    if not isinstance(stdout_len, int) or not isinstance(stderr_len, int):
        return emit("rejected", 0, bound)
    if stdout_len < 0 or stderr_len < 0 or len(raw) != cursor + stdout_len + stderr_len:
        return emit("rejected", 0, bound)

    stdout_bytes = raw[cursor : cursor + stdout_len]
    cursor += stdout_len
    stderr_bytes = raw[cursor : cursor + stderr_len]

    if hashlib.sha256(stdout_bytes).hexdigest() != stdout_info.get("digest_sha256"):
        return emit("rejected", 0, bound)
    if hashlib.sha256(stderr_bytes).hexdigest() != stderr_info.get("digest_sha256"):
        return emit("rejected", 0, bound)

    if container.get("exit_code") != 0 or container.get("state") != "exited":
        return emit("rejected", 0, bound)
    if container.get("timed_out") or container.get("oom_killed") or container.get("overflowed"):
        return emit("rejected", 0, bound)

    try:
        snapshot = json.loads(
            stdout_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return emit("rejected", 0, bound)

    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_KEYS:
        return emit("rejected", 0, bound)
    if snapshot.get("status") != "executed" or snapshot.get("error") is not None:
        return emit("rejected", 0, bound)

    submission = snapshot.get("submission")
    if not isinstance(submission, dict):
        return emit("rejected", 0, bound)

    # Load expected files
    expected_path = Path("/opt/rolebench/task/verifier-private/expected_files.json")
    if not expected_path.is_file():
        expected_path = Path(__file__).resolve().parent / "expected_files.json"

    try:
        expected_files = json.loads(expected_path.read_text(encoding="utf-8"))
    except OSError:
        return emit("rejected", 0, bound)

    if not _verify_submission(submission, expected_files):
        return emit("rejected", 0, bound)

    return emit("accepted", 1, bound)


if __name__ == "__main__":
    raise SystemExit(main())
