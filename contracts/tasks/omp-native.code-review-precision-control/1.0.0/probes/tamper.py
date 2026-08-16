#!/usr/bin/env python3
"""Tamper probe reporting hallucinated memory leak on Python list."""

import json

submission = {
    "schema_version": "rolebench.review-findings/v1",
    "verdict": "changes_requested",
    "findings": [
        {
            "file": "src/ring_buffer.py",
            "line_start": 30,
            "line_end": 38,
            "severity": "high",
            "category": "resource-leak",
            "description": "Fabricated issue: pop() method leaks memory because Python list does not deallocate None.",
        }
    ],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
