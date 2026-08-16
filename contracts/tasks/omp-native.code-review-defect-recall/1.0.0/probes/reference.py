#!/usr/bin/env python3
"""Exact reference review probe identifying all three material defects."""

import json

submission = {
    "schema_version": "rolebench.review-findings/v1",
    "verdict": "changes_requested",
    "findings": [
        {
            "file": "src/cache.py",
            "line_start": 16,
            "line_end": 28,
            "severity": "high",
            "category": "concurrency",
            "description": "Unsynchronized mutation of shared _inflight dictionary across await points allows race conditions and corrupted future states.",
        },
        {
            "file": "src/storage.py",
            "line_start": 11,
            "line_end": 14,
            "severity": "medium",
            "category": "resource-leak",
            "description": "File handle f is opened without a context manager (with statement), causing file descriptor leak if an exception occurs before close.",
        },
        {
            "file": "src/auth.py",
            "line_start": 10,
            "line_end": 13,
            "severity": "high",
            "category": "security",
            "description": "Token verification uses non-constant-time equality comparison (==) which is vulnerable to timing side-channel attacks. Use hmac.compare_digest.",
        },
    ],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
