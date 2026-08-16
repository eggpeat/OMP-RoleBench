#!/usr/bin/env python3
"""Known-good image-code admission probe."""

import hashlib
import json
from pathlib import Path

image = Path("/opt/rolebench/task/public/workspace/code.png").read_bytes()
h0 = hashlib.sha256(image).digest()
digest = hashlib.sha256(h0 + h0[7:19] + b"ROLEBENCH-7Q9X").hexdigest()
print(json.dumps({"schema_version": "rolebench.image-code-answer/v1", "salt": "ROLEBENCH-7Q9X", "slice_start": 7, "slice_end": 19, "digest_sha256": digest}, sort_keys=True, separators=(",", ":")))
