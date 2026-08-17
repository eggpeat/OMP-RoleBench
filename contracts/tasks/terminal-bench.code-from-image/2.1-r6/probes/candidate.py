#!/usr/bin/env python3
"""Synthetic vision-candidate admission probe."""

import json

GROUND_TRUTH = {
    "schema_version": "rolebench.code-flowchart-graph/v1",
    "pipeline_name": "request-routing-pipeline",
    "lanes": [
        "ingress",
        "auth_guard",
        "rate_policy",
        "execution",
    ],
    "nodes": [
        {
            "id": "recv_request",
            "kind": "start",
            "lane": "ingress",
            "label": "recv_request(payload, headers)",
        },
        {
            "id": "parse_header",
            "kind": "process",
            "lane": "ingress",
            "label": "auth_token, client_id = parse_headers(headers)",
        },
        {
            "id": "verify_token",
            "kind": "decision",
            "lane": "auth_guard",
            "label": "is_valid_jwt(auth_token)",
        },
        {
            "id": "reject_unauthorized",
            "kind": "terminal",
            "lane": "auth_guard",
            "label": 'http_response(401, "UNAUTHORIZED")',
        },
        {
            "id": "lookup_tier",
            "kind": "process",
            "lane": "rate_policy",
            "label": "tier = get_tier(client_id)",
        },
        {
            "id": "check_enterprise",
            "kind": "decision",
            "lane": "rate_policy",
            "label": 'tier == "ENTERPRISE"',
        },
        {
            "id": "set_enterprise_limit",
            "kind": "process",
            "lane": "rate_policy",
            "label": "max_tokens = 5000, refill_rate = 100",
        },
        {
            "id": "set_standard_limit",
            "kind": "process",
            "lane": "rate_policy",
            "label": "max_tokens = 200, refill_rate = 10",
        },
        {
            "id": "consume_token",
            "kind": "process",
            "lane": "rate_policy",
            "label": "allowed = try_consume(client_id, max_tokens, refill_rate)",
        },
        {
            "id": "check_rate_limit",
            "kind": "decision",
            "lane": "rate_policy",
            "label": "allowed == true",
        },
        {
            "id": "reject_rate_limit",
            "kind": "terminal",
            "lane": "rate_policy",
            "label": 'http_response(429, "RATE_LIMIT_EXCEEDED")',
        },
        {
            "id": "dispatch_handler",
            "kind": "process",
            "lane": "execution",
            "label": "result = invoke_route_handler(payload)",
        },
        {
            "id": "emit_success",
            "kind": "terminal",
            "lane": "execution",
            "label": "http_response(200, result)",
        },
    ],
    "edges": [
        {"source": "recv_request", "target": "parse_header", "condition": "unconditional"},
        {"source": "parse_header", "target": "verify_token", "condition": "unconditional"},
        {"source": "verify_token", "target": "lookup_tier", "condition": "valid"},
        {"source": "verify_token", "target": "reject_unauthorized", "condition": "invalid"},
        {"source": "lookup_tier", "target": "check_enterprise", "condition": "unconditional"},
        {"source": "check_enterprise", "target": "set_enterprise_limit", "condition": "enterprise"},
        {"source": "check_enterprise", "target": "set_standard_limit", "condition": "standard"},
        {"source": "set_enterprise_limit", "target": "consume_token", "condition": "unconditional"},
        {"source": "set_standard_limit", "target": "consume_token", "condition": "unconditional"},
        {"source": "consume_token", "target": "check_rate_limit", "condition": "unconditional"},
        {"source": "check_rate_limit", "target": "dispatch_handler", "condition": "allowed"},
        {"source": "check_rate_limit", "target": "reject_rate_limit", "condition": "throttled"},
        {"source": "dispatch_handler", "target": "emit_success", "condition": "unconditional"},
    ],
}

print(json.dumps(GROUND_TRUTH, sort_keys=True, separators=(",", ":")))
