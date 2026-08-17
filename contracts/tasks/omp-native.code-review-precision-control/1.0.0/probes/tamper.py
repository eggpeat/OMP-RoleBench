#!/usr/bin/env python3
"""Tamper probe reporting false positive on double-checked locking distractor."""

import json

submission = {
    "schema_version": "rolebench.review-findings/v1",
    "verdict": "changes_requested",
    "findings": [
        {
            "file": "src/ring_buffer.py",
            "line_start": 60,
            "line_end": 77,
            "severity": "high",
            "category": "concurrency",
            "description": "False positive: drain() uses unsafe double-checked locking pattern for empty check.",
        }
    ],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
