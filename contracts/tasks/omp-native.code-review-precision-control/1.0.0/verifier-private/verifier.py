#!/usr/bin/env python3
"""Passive exact-result verifier for structured code review precision control task."""

from __future__ import annotations

import json
import sys

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"


def verify_submission(submission: dict) -> tuple[bool, float, str, dict]:
    if not isinstance(submission, dict):
        return False, 0.0, "submission is not a dictionary", {}

    verdict = submission.get("verdict")
    findings = submission.get("findings", [])
    if not isinstance(findings, list):
        return False, 0.0, "findings must be a list", {}

    n_fp = len(findings)
    details = {
        "verdict": verdict,
        "false_positive_count": n_fp,
        "findings_reported": findings,
    }

    if verdict != "approved":
        details["score"] = 0.0
        return False, 0.0, f"expected verdict 'approved' on clean change, got {verdict!r} with {n_fp} false-positive finding(s)", details

    # Calculate score: full credit 1.0 only for zero findings, penalized for each false positive
    score = max(0.0, 1.0 - 0.5 * n_fp)
    details["score"] = score

    if n_fp == 0:
        return True, 1.0, "clean change correctly approved with zero false positives", details

    return False, score, f"reported {n_fp} false-positive finding(s) on clean change; score penalized to {score:.2f}", details


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
    passed, score, reason, details = verify_submission(submission)

    out = {
        "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
        "verdict": "pass" if passed else "fail",
        "score": score,
        "reason": reason,
        "details": details,
    }
    print(json.dumps(out, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
