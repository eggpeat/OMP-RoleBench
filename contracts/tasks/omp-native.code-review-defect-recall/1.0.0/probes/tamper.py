#!/usr/bin/env python3
"""Tamper probe reporting hallucinated / fabricated defects on non-defective lines."""

import json

submission = {
    "schema_version": "rolebench.review-findings/v1",
    "verdict": "changes_requested",
    "findings": [
        {
            "file": "src/cache.py",
            "line_start": 4,
            "line_end": 6,
            "severity": "high",
            "category": "concurrency",
            "description": "Fabricated issue: constructor __init__ cannot assign instance variables.",
        },
        {
            "file": "src/auth.py",
            "line_start": 5,
            "line_end": 7,
            "severity": "high",
            "category": "security",
            "description": "Fabricated issue: secret key storage in instance variable violates cryptographic isolation.",
        },
    ],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
