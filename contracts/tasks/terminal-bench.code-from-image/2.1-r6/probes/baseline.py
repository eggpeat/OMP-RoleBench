#!/usr/bin/env python3
"""Deliberately ungrounded baseline for the image-code task."""

import json

print(json.dumps({"schema_version": "rolebench.image-code-answer/v1", "salt": "UNKNOWN", "slice_start": 0, "slice_end": 1, "digest_sha256": "0" * 64}, sort_keys=True, separators=(",", ":")))
