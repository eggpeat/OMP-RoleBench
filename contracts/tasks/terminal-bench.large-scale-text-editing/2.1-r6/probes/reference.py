#!/usr/bin/env python3
"""Known-good admission probe for the Vim macro task."""

import json

script = """call setreg('a', ":s/\\\\s*,\\\\s*/,/g\\<CR>:s/^\\\\s*\\\\(.*\\\\S\\\\)\\\\s*$/\\\\1/\\<CR>j")
call setreg('b', ":s/^\\\\([^,]*\\\\),\\\\([^,]*\\\\),\\\\([^,]*\\\\)$/\\\\3,\\\\2,\\\\1/\\<CR>j")
call setreg('c', ":s/^\\\\([^,]*\\\\),\\\\([^,]*\\\\),\\\\([^,]*\\\\)$/\\\\U\\\\1\\\\E,\\\\U\\\\2\\\\E,\\\\U\\\\3\\\\E/\\<CR>:s/,/;/g\\<CR>:s/$/;OK/\\<CR>j")
%normal! @a
%normal! @b
%normal! @c
wq
"""
print(json.dumps({"schema_version": "rolebench.vim-macro-submission/v1", "script": script}, sort_keys=True, separators=(",", ":")))
