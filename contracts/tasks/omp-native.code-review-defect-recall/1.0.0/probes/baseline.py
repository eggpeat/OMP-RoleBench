#!/usr/bin/env python3
"""Deliberately shallow review baseline that approves without finding defects."""

import json

submission = {
    "schema_version": "rolebench.review-findings/v1",
    "verdict": "approved",
    "findings": [],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
