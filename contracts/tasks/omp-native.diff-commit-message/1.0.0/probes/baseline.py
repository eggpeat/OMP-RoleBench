#!/usr/bin/env python3
"""Deliberately generic commit-message baseline."""

import json

submission = {
    "schema_version": "rolebench.commit-message/v1",
    "type": "feat",
    "scope": "cache",
    "subject": "update cache code",
    "body": ["Update the implementation."],
    "evidence": ["change.patch:1-35"],
    "breaking": False,
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
