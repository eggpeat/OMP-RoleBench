#!/usr/bin/env python3
"""Deliberately incomplete security-review baseline."""

import json

submission = {
    "schema_version": "rolebench.security-review/v1",
    "backdoor_present": False,
    "findings": [],
    "reviewed_safe": ["auth_handler", "authfile_check", "parse_auth_token", "audit_user"],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
