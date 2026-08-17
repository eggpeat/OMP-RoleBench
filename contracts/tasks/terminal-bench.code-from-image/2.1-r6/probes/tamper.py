#!/usr/bin/env python3
"""Tamper probe with a plausible but false/hallucinated flowchart graph structure."""

import json

tamper_payload = {
    "schema_version": "rolebench.code-flowchart-graph/v1",
    "pipeline_name": "request-routing-pipeline",
    "lanes": [
        "ingress",
        "auth_gate",
        "quota_manager",
        "execution_backend",
    ],
    "nodes": [
        {
            "id": "start_job",
            "kind": "start",
            "lane": "ingress",
            "label": "start_session()",
        },
        {
            "id": "check_auth",
            "kind": "decision",
            "lane": "auth_gate",
            "label": "auth_check()",
        },
        {
            "id": "process_step",
            "kind": "process",
            "lane": "quota_manager",
            "label": "allocate_tokens()",
        },
        {
            "id": "finish_job",
            "kind": "terminal",
            "lane": "execution_backend",
            "label": "return_response(200)",
        },
    ],
    "edges": [
        {"source": "start_job", "target": "check_auth", "condition": "unconditional"},
        {"source": "check_auth", "target": "process_step", "condition": "yes"},
        {"source": "process_step", "target": "finish_job", "condition": "done"},
    ],
}

print(json.dumps(tamper_payload, sort_keys=True, separators=(",", ":")))
