#!/usr/bin/env python3
"""Passive exact-result verifier for structured code review precision control task."""

from __future__ import annotations

import json
import sys

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"


def verify_submission(submission: dict) -> tuple[bool, str, dict]:
    if not isinstance(submission, dict):
        return False, "submission is not a dictionary", {}

    verdict = submission.get("verdict")
    findings = submission.get("findings", [])
    if not isinstance(findings, list):
        return False, "findings must be a list", {}

    details = {
        "verdict": verdict,
        "false_positive_count": len(findings),
        "findings_reported": findings,
    }

    if verdict != "approved":
        return False, f"expected verdict 'approved' on clean change, got {verdict!r}", details

    if len(findings) > 0:
        return False, f"reported {len(findings)} false-positive findings on clean change", details

    return True, "clean change correctly approved with zero false positives", details


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
