#!/usr/bin/env python3
"""Deliberately over-sensitive baseline probe reporting false positive on clean code."""

import json

submission = {
    "schema_version": "rolebench.review-findings/v1",
    "verdict": "changes_requested",
    "findings": [
        {
            "file": "src/ring_buffer.py",
            "line_start": 47,
            "line_end": 52,
            "severity": "medium",
            "category": "correctness",
            "description": "False positive: DeprecationWarning in LegacyBuffer should be removed.",
        }
    ],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
