#!/usr/bin/env python3
"""Evidence-resolving but false commit-message tamper probe."""

import json

submission = {
    "schema_version": "rolebench.commit-message/v1",
    "type": "fix",
    "scope": "cache",
    "subject": "do not invalidate l1 before backing-store deletion",
    "body": [
        "Do not invalidate the L1 entry before deleting from the backing store.",
        "Do not record hit and miss outcomes for cache deletions.",
    ],
    "evidence": ["change.patch:7-9", "change.patch:10-11"],
    "breaking": False,
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
