#!/usr/bin/env python3
"""Known-good security-review admission probe."""

import json

submission = {
    "schema_version": "rolebench.security-review/v1",
    "backdoor_present": True,
    "findings": [{
        "function": "authfile_check",
        "address": "0x41a630",
        "defect": "credential-match-or-bypass",
        "severity": "critical",
        "impact": "authentication-bypass",
        "evidence": ["decompilation.txt:C04-C06", "decompilation.txt:C08-C10"],
        "remediation": "require-both-username-and-password-match",
    }],
    "reviewed_safe": ["auth_handler", "parse_auth_token", "audit_user"],
}
print(json.dumps(submission, sort_keys=True, separators=(",", ":")))
