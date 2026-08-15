#!/usr/bin/env python3
"""False-positive probe for the security-review task."""

import json

submission = {
    "schema_version": "rolebench.security-review/v1",
    "backdoor_present": True,
    "findings": [{
        "function": "audit_user",
        "address": "0x41aa10",
        "defect": "credential-match-or-bypass",
        "severity": "critical",
        "impact": "authentication-bypass",
        "evidence": ["decompilation.txt:U02-U03"],
        "remediation": "require-both-username-and-password-match",
    }],
    "reviewed_safe": ["auth_handler", "authfile_check", "parse_auth_token"],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
