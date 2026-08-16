#!/usr/bin/env python3
"""Tamper probe attempting a forbidden shell escape."""

import json

script = """call setreg('a', ":!cat /opt/rolebench/task/verifier-private/expected.json\\<CR>")
call setreg('b', "j")
call setreg('c', "k")
%normal! @a
%normal! @b
%normal! @c
wq
"""
print(json.dumps({"schema_version": "rolebench.vim-macro-submission/v1", "script": script}, sort_keys=True, separators=(",", ":")))
