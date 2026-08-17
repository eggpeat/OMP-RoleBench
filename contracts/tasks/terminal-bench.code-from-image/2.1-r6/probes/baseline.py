#!/usr/bin/env python3
"""Deliberately ungrounded baseline for the flowchart graph task."""

import json

payload = {
    "schema_version": "rolebench.code-flowchart-graph/v1",
    "pipeline_name": "request-routing-pipeline",
    "lanes": [],
    "nodes": [],
    "edges": [],
}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
