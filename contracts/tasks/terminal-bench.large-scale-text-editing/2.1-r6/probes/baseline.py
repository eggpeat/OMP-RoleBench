#!/usr/bin/env python3
"""Deliberately incomplete baseline probe for the Vim macro task."""

import json

print(json.dumps({"schema_version": "rolebench.vim-macro-submission/v1", "script": "wq\n"}, sort_keys=True, separators=(",", ":")))
