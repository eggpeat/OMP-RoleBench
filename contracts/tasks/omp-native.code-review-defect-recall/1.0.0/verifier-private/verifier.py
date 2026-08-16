#!/usr/bin/env python3
"""Passive exact-result verifier for structured code review defect recall task."""

from __future__ import annotations

import json
import sys

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"

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


def _matches_target(finding: dict, target: dict) -> bool:
    finding_file = finding.get("file", "")
    if not any(finding_file.endswith(pat) or pat.endswith(finding_file) for pat in target["file_patterns"]):
        return False
    start = finding.get("line_start", 0)
    end = finding.get("line_end", 0)
    t_start, t_end = target["line_range"]
    # Check for line range overlap
    if end < t_start or start > t_end:
        return False
    category = finding.get("category")
    if category not in target["expected_categories"]:
        return False
    return True


def verify_submission(submission: dict) -> tuple[bool, str, dict]:
    if not isinstance(submission, dict):
        return False, "submission is not a dictionary", {}

    verdict = submission.get("verdict")
    if verdict != "changes_requested":
        return False, f"expected verdict 'changes_requested', got {verdict!r}", {}

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
    recall = len(matched_targets) / len(SEEDED_DEFECTS)

    details = {
        "total_seeded_defects": len(SEEDED_DEFECTS),
        "recalled_defects": len(matched_targets),
        "recall_ratio": recall,
        "false_positive_count": false_positive_count,
        "matched_defect_ids": sorted(matched_targets),
    }

    if recall < 1.0:
        missing = [t["id"] for t in SEEDED_DEFECTS if t["id"] not in matched_targets]
        return False, f"missed material defects: {missing!r}", details

    if false_positive_count > 0:
        return False, f"reported {false_positive_count} unsupported/fabricated findings", details

    return True, "all seeded defects recalled with zero false positives", details


def main() -> int:
    raw = sys.stdin.buffer.read()
    if not raw.startswith(RUNNER_EVIDENCE_MAGIC):
        out = {
            "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
            "verdict": "fail",
            "score": 0.0,
            "reason": "invalid runner evidence magic header",
        }
        print(json.dumps(out, sort_keys=True, separators=(",", ":")))
        return 0

    body = raw[len(RUNNER_EVIDENCE_MAGIC):]
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
            "reason": f"malformed evidence envelope: {err}",
        }
        print(json.dumps(out, sort_keys=True, separators=(",", ":")))
        return 0

    if snapshot.get("status") != "accepted":
        out = {
            "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
            "verdict": "fail",
            "score": 0.0,
            "reason": f"runner rejected submission: {snapshot.get('error')}",
        }
        print(json.dumps(out, sort_keys=True, separators=(",", ":")))
        return 0

    submission = snapshot.get("submission")
    passed, reason, details = verify_submission(submission)

    out = {
        "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
        "verdict": "pass" if passed else "fail",
        "score": 1.0 if passed else 0.0,
        "reason": reason,
        "details": details,
    }
    print(json.dumps(out, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
