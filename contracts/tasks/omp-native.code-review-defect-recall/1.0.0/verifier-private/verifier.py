#!/usr/bin/env python3
"""Passive exact-result verifier for structured code review defect recall task."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from typing import Any, NoReturn

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024

HEADER_KEYS = {
    "schema_version",
    "run_id",
    "attempt_nonce",
    "task_digest_sha256",
    "policy_digest_sha256",
    "artifact_digest_sha256",
    "evaluation_request_digest_sha256",
    "verifier_image_digest_sha256",
    "container",
    "stdout",
    "stderr",
}
CONTAINER_KEYS = {
    "state",
    "exit_code",
    "oom_killed",
    "timed_out",
    "overflowed",
    "removed",
    "duration_seconds",
}
IO_KEYS = {"authority", "byte_count", "digest_sha256"}

# Target seeded defects in change.patch
SEEDED_DEFECTS = [
    {
        "id": "cache_concurrency_race",
        "file_patterns": ["cache.py", "src/cache.py"],
        "line_range": (10, 30),
        "expected_categories": {"concurrency", "correctness"},
        "min_severity": "medium",
    },
    {
        "id": "storage_descriptor_leak",
        "file_patterns": ["storage.py", "src/storage.py"],
        "line_range": (8, 16),
        "expected_categories": {"resource-leak", "correctness"},
        "min_severity": "medium",
    },
    {
        "id": "auth_timing_side_channel",
        "file_patterns": ["auth.py", "src/auth.py"],
        "line_range": (8, 15),
        "expected_categories": {"security"},
        "min_severity": "medium",
    },
]

SEVERITY_RANKS = {"low": 1, "medium": 2, "high": 3}


class EvidenceError(ValueError):
    """Malformed evidence framing."""


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
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _matches_file(finding_file: str, allowed_patterns: list[str]) -> bool:
    norm_finding = Path(finding_file.strip()).as_posix().removeprefix("./").removeprefix("/")
    allowed_clean = {Path(p.strip()).as_posix().removeprefix("./").removeprefix("/") for p in allowed_patterns}
    return norm_finding in allowed_clean


def _matches_target(finding: dict, target: dict) -> bool:
    finding_file = finding.get("file", "")
    if not _matches_file(finding_file, target["file_patterns"]):
        return False
    start = finding.get("line_start", 0)
    end = finding.get("line_end", 0)
    t_start, t_end = target["line_range"]
    if end < t_start or start > t_end:
        return False
    category = finding.get("category")
    if category not in target["expected_categories"]:
        return False
    min_sev = target.get("min_severity", "low")
    finding_sev = finding.get("severity", "low")
    if SEVERITY_RANKS.get(finding_sev, 0) < SEVERITY_RANKS.get(min_sev, 0):
        return False
    return True


def verify_submission(submission: dict) -> tuple[bool, str, dict]:
    if not isinstance(submission, dict):
        return False, "submission is not a dictionary", {}

    verdict = submission.get("verdict")
    findings = submission.get("findings", [])
    if not isinstance(findings, list):
        return False, "findings must be a list", {}

    matched_targets: set[str] = set()
    matched_findings: set[int] = set()

    for target in SEEDED_DEFECTS:
        for idx, finding in enumerate(findings):
            if idx in matched_findings:
                continue
            if _matches_target(finding, target):
                matched_targets.add(target["id"])
                matched_findings.add(idx)
                break

    false_positive_count = len(findings) - len(matched_findings)
    recalled_count = len(matched_targets)
    total_defects = len(SEEDED_DEFECTS)
    recall = recalled_count / total_defects

    score = 0.0
    if verdict == "changes_requested":
        score = max(0.0, round((recalled_count - false_positive_count) / total_defects, 4))

    details = {
        "total_seeded_defects": total_defects,
        "recalled_defects": recalled_count,
        "recall_ratio": recall,
        "false_positive_count": false_positive_count,
        "matched_defect_ids": sorted(matched_targets),
        "score": score,
    }

    if verdict != "changes_requested":
        return False, f"expected verdict 'changes_requested', got {verdict!r}", details

    if recall == 1.0 and false_positive_count == 0:
        return True, "all seeded defects recalled with zero false positives", details

    if false_positive_count > 0 and recall < 1.0:
        missing = [t["id"] for t in SEEDED_DEFECTS if t["id"] not in matched_targets]
        return False, f"missed material defects: {missing!r} and reported {false_positive_count} unsupported findings", details

    if false_positive_count > 0:
        return False, f"reported {false_positive_count} unsupported/fabricated findings", details

    missing = [t["id"] for t in SEEDED_DEFECTS if t["id"] not in matched_targets]
    return False, f"missed material defects: {missing!r}", details


def _emit_result(
    *,
    outcome: str,
    reward: float | None,
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
    magic_len = len(RUNNER_EVIDENCE_MAGIC)

    # Check for worker-framed evidence (starts with RUNNER_EVIDENCE_MAGIC + 8-byte big-endian header length)
    if raw.startswith(RUNNER_EVIDENCE_MAGIC) and len(raw) >= magic_len + 8:
        try:
            header_length = struct.unpack(">Q", raw[magic_len : magic_len + 8])[0]
            header_start = magic_len + 8
            header_end = header_start + header_length
            if header_end <= len(raw):
                header_bytes = raw[header_start:header_end]
                header = json.loads(
                    header_bytes.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                )
                if (
                    isinstance(header, dict)
                    and header.get("schema_version") == RUNNER_EVIDENCE_SCHEMA_VERSION
                    and "run_id" in header
                ):
                    fallback = {
                        "run_id": str(header.get("run_id", "unknown")),
                        "attempt_nonce": str(header.get("attempt_nonce", "0" * 64)),
                        "artifact_digest_sha256": str(header.get("artifact_digest_sha256", "0" * 64)),
                        "evaluation_request_digest_sha256": str(header.get("evaluation_request_digest_sha256", "0" * 64)),
                        "verifier_image_digest_sha256": str(header.get("verifier_image_digest_sha256", "0" * 64)),
                    }
                    def emit(outcome: str, reward: float | None, values: dict[str, str] = fallback) -> int:
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

                    stdout_info = header.get("stdout")
                    if not isinstance(stdout_info, dict):
                        return emit("rejected", 0.0)
                    stdout_len = stdout_info.get("byte_count", 0)
                    stdout_start = header_end
                    stdout_end = stdout_start + stdout_len
                    if stdout_end > len(raw):
                        return emit("rejected", 0.0)
                    stdout_bytes = raw[stdout_start:stdout_end]

                    # Parse runner snapshot from stdout_bytes
                    snap_body = stdout_bytes
                    if snap_body.startswith(RUNNER_EVIDENCE_MAGIC):
                        snap_body = snap_body[magic_len:]
                        newline_idx = snap_body.index(b"\n")
                        length = int(snap_body[:newline_idx].decode("ascii"))
                        snap_body = snap_body[newline_idx + 1: newline_idx + 1 + length]
                    snapshot = json.loads(snap_body.decode("utf-8"))
                    if snapshot.get("status") != "accepted":
                        return emit("rejected", 0.0)
                    submission = snapshot.get("submission")
                    passed, reason, details = verify_submission(submission)
                    score = details.get("score", 0.0)
                    if passed and score == 1.0:
                        return emit("accepted", 1.0)
                    else:
                        return emit("rejected", score)
        except Exception:
            pass

    # Homemade un-framed format (used by standalone probe tests or local calibration)
    if raw.startswith(RUNNER_EVIDENCE_MAGIC):
        body = raw[magic_len:]
        try:
            newline_idx = body.index(b"\n")
            length = int(body[:newline_idx].decode("ascii"))
            payload_bytes = body[newline_idx + 1: newline_idx + 1 + length]
            snapshot = json.loads(payload_bytes.decode("utf-8"))
        except Exception as err:
            out = {
                "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
                "verdict": "fail",
                "score": 0.0,
                "reward": 0.0,
                "reason": f"malformed evidence envelope: {err}",
            }
            print(json.dumps(out, sort_keys=True, separators=(",", ":")))
            return 0
    else:
        try:
            snapshot = json.loads(raw.decode("utf-8"))
        except Exception as err:
            out = {
                "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
                "verdict": "fail",
                "score": 0.0,
                "reward": 0.0,
                "reason": f"malformed json: {err}",
            }
            print(json.dumps(out, sort_keys=True, separators=(",", ":")))
            return 0

    if snapshot.get("status") != "accepted":
        out = {
            "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
            "verdict": "fail",
            "score": 0.0,
            "reward": 0.0,
            "reason": f"runner rejected submission: {snapshot.get('error')}",
        }
        print(json.dumps(out, sort_keys=True, separators=(",", ":")))
        return 0

    submission = snapshot.get("submission")
    passed, reason, details = verify_submission(submission)
    score = details.get("score", 1.0 if passed else 0.0)

    out = {
        "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
        "verdict": "pass" if passed else "fail",
        "score": score,
        "reward": score,
        "reason": reason,
        "details": details,
    }
    print(json.dumps(out, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
