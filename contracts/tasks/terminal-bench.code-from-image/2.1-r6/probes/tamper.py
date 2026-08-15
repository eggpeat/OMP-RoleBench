#!/usr/bin/env python3
"""Tamper probe with a plausible but false image-derived answer."""

import json

print(json.dumps({"schema_version": "rolebench.image-code-answer/v1", "salt": "ROLEBENCH-7Q9X", "slice_start": 7, "slice_end": 19, "digest_sha256": "f" * 64}, sort_keys=True, separators=(",", ":")))
