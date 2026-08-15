#!/usr/bin/env python3
"""Deliberately incomplete designer-role baseline."""

import json

html = '<!doctype html><html lang="en"><head><link rel="stylesheet" href="styles.css"></head><body><main><h1>Relay Incident Console</h1></main></body></html>'
css = 'body { margin: 0; }'
print(json.dumps({"schema_version": "rolebench.ui-implementation/v1", "files": {"index.html": html, "styles.css": css}}, sort_keys=True, separators=(",", ":")))
