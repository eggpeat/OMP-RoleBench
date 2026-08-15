#!/usr/bin/env python3
"""Evidence-resolving but false commit-message tamper probe."""

import json

submission = {
    "schema_version": "rolebench.commit-message/v1",
    "type": "fix",
    "scope": "cache",
    "subject": "encrypt cache entries at rest",
    "body": ["Encrypt cached values before persistence."],
    "evidence": ["change.patch:9-11"],
    "breaking": False,
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
