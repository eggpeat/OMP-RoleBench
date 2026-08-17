#!/usr/bin/env python3
"""Deliberately no-op baseline probe for the mechanical refactoring task."""

import json

SUBMISSION = {
    "schema_version": "rolebench.text-editing-submission/v1",
    "modified_files": {},
}

if __name__ == "__main__":
    print(json.dumps(SUBMISSION, sort_keys=True, separators=(",", ":")))
