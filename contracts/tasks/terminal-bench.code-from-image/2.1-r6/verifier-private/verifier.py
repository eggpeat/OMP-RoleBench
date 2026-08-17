#!/usr/bin/env python3
"""Passive exact-result verifier for the image-grounded flowchart graph task."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from typing import Any, NoReturn

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
HEADER_KEYS = {
    "schema_version",
    "attempt_nonce",
    "run_id",
    "task_digest_sha256",
    "policy_digest_sha256",
    "artifact_digest_sha256",
    "evaluation_request_digest_sha256",
    "verifier_image_digest_sha256",
    "runner",
    "container",
    "stdout",
    "stderr",
}
CONTAINER_KEYS = {
    "container_id",
    "state",
    "exit_code",
    "oom_killed",
    "timed_out",
    "overflowed",
    "duration_seconds",
    "removed",
}
IO_KEYS = {"byte_count", "digest_sha256", "authority"}
SNAPSHOT_KEYS = {"status", "submission", "error", "flowchart_sha256", "schema_version"}

EXPECTED_FLOWCHART_SHA256 = "947ca16dd6c890707c1aa9be7dbf639ce7dcc8d0cc65262574396f6d60064aa3"

GROUND_TRUTH: dict[str, Any] = {
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


class EvidenceError(ValueError):
    """Malformed runner evidence or result payload."""


def _reject_constant(value: str) -> NoReturn:
    raise EvidenceError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalize_text(text: str) -> str:
    cleaned = text.replace(",", " ").replace(";", " ").replace("\n", " ")
    return " ".join(cleaned.strip().lower().split())


def _score_submission(submission: object) -> tuple[bool, float, dict[str, Any]]:
    if not isinstance(submission, dict):
        return False, 0.0, {"error": "submission is not a dictionary"}

    # 1. Lanes scoring
    sub_lanes = submission.get("lanes", [])
    exp_lanes = GROUND_TRUTH["lanes"]
    if isinstance(sub_lanes, list) and sub_lanes:
        matched_lane_pos = sum(1 for a, b in zip(sub_lanes, exp_lanes) if a == b)
        lane_score = matched_lane_pos / max(len(exp_lanes), len(sub_lanes))
    else:
        lane_score = 0.0

    # 2. Nodes scoring
    sub_nodes = submission.get("nodes", [])
    exp_nodes = GROUND_TRUTH["nodes"]
    node_points = 0.0
    matched_sub_node_ids: set[str] = set()

    if isinstance(sub_nodes, list):
        sub_node_map: dict[str, dict[str, Any]] = {}
        for n in sub_nodes:
            if isinstance(n, dict) and "id" in n:
                sub_node_map[str(n["id"])] = n

        for exp in exp_nodes:
            eid = exp["id"]
            if eid in sub_node_map:
                matched_sub_node_ids.add(eid)
                cand = sub_node_map[eid]
                pt = 0.25
                if cand.get("kind") == exp["kind"]:
                    pt += 0.25
                if cand.get("lane") == exp["lane"]:
                    pt += 0.25
                cand_lbl = _normalize_text(str(cand.get("label", "")))
                exp_lbl = _normalize_text(exp["label"])
                if cand_lbl == exp_lbl:
                    pt += 0.25
                elif cand_lbl and (exp_lbl in cand_lbl or cand_lbl in exp_lbl):
                    pt += 0.15
                node_points += pt

        extra_nodes = len(sub_node_map) - len(matched_sub_node_ids)
        node_penalty = extra_nodes * (1.0 / len(exp_nodes))
        node_score = max(0.0, (node_points / len(exp_nodes)) - node_penalty)
    else:
        node_score = 0.0

    # 3. Edges scoring
    sub_edges = submission.get("edges", [])
    exp_edges = GROUND_TRUTH["edges"]
    edge_points = 0.0

    if isinstance(sub_edges, list):
        sub_edge_pairs: set[tuple[str, str]] = set()
        sub_edge_map: dict[tuple[str, str], str] = {}
        for e in sub_edges:
            if isinstance(e, dict) and "source" in e and "target" in e:
                pair = (str(e["source"]), str(e["target"]))
                sub_edge_pairs.add(pair)
                sub_edge_map[pair] = str(e.get("condition", ""))

        matched_edges = 0
        for exp in exp_edges:
            pair = (exp["source"], exp["target"])
            if pair in sub_edge_map:
                matched_edges += 1
                pt = 0.60
                cand_cond = _normalize_text(sub_edge_map[pair])
                exp_cond = _normalize_text(exp["condition"])
                if cand_cond == exp_cond:
                    pt += 0.40
                edge_points += pt

        extra_edges = len(sub_edge_pairs) - matched_edges
        edge_penalty = extra_edges * (1.0 / len(exp_edges))
        edge_score = max(0.0, (edge_points / len(exp_edges)) - edge_penalty)
    else:
        edge_score = 0.0

    # Composite reward: 50% nodes, 40% edges, 10% lanes
    reward = round(0.50 * node_score + 0.40 * edge_score + 0.10 * lane_score, 4)
    reward = max(0.0, min(1.0, reward))
    passed = (reward >= 0.95)

    details = {
        "lane_score": round(lane_score, 4),
        "node_score": round(node_score, 4),
        "edge_score": round(edge_score, 4),
        "reward": reward,
        "passed": passed,
    }
    return passed, reward, details


def _emit_result(
    *,
    outcome: str,
    reward: float | None,
    run_id: str,
    attempt_nonce: str,
    artifact_digest_sha256: str,
    runner_evidence_digest_sha256: str,
    evaluation_request_digest_sha256: str,
    verifier_image_digest_sha256: str,
) -> int:
    result = {
        "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "attempt_nonce": attempt_nonce,
        "outcome": outcome,
        "reward": reward,
        "artifact_digest_sha256": artifact_digest_sha256,
        "runner_evidence_digest_sha256": runner_evidence_digest_sha256,
        "evaluation_request_digest_sha256": evaluation_request_digest_sha256,
        "verifier_image_digest_sha256": verifier_image_digest_sha256,
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    evidence_digest = hashlib.sha256(raw).hexdigest()
    fallback = {
        "run_id": "unknown",
        "attempt_nonce": "0" * 64,
        "artifact_digest_sha256": "0" * 64,
        "evaluation_request_digest_sha256": "0" * 64,
        "verifier_image_digest_sha256": "0" * 64,
    }

    def emit(outcome: str, reward: float | None, values: dict[str, str] = fallback) -> int:
        return _emit_result(
            outcome=outcome,
            reward=reward,
            run_id=values["run_id"],
            attempt_nonce=values["attempt_nonce"],
            artifact_digest_sha256=values["artifact_digest_sha256"],
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256=values["evaluation_request_digest_sha256"],
            verifier_image_digest_sha256=values["verifier_image_digest_sha256"],
        )

    if not raw or len(raw) > MAX_EVIDENCE_BYTES:
        return emit("error", None)
    magic_len = len(RUNNER_EVIDENCE_MAGIC)
    if not raw.startswith(RUNNER_EVIDENCE_MAGIC) or len(raw) < magic_len + 8:
        return emit("error", None)
    header_length = struct.unpack(">Q", raw[magic_len : magic_len + 8])[0]
    header_start = magic_len + 8
    header_end = header_start + header_length
    if header_end > len(raw):
        return emit("error", None)
    header_bytes = raw[header_start:header_end]
    try:
        header = json.loads(
            header_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return emit("error", None)
    if (
        not isinstance(header, dict)
        or set(header) != HEADER_KEYS
        or header_bytes != json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        or header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION
        or not isinstance(header.get("run_id"), str)
        or not header.get("run_id")
        or any(
            not _is_sha256(header.get(field))
            for field in (
                "attempt_nonce",
                "task_digest_sha256",
                "policy_digest_sha256",
                "artifact_digest_sha256",
                "evaluation_request_digest_sha256",
                "verifier_image_digest_sha256",
            )
        )
    ):
        return emit("error", None)
    bound = {
        "run_id": str(header["run_id"]),
        "attempt_nonce": str(header["attempt_nonce"]),
        "artifact_digest_sha256": str(header["artifact_digest_sha256"]),
        "evaluation_request_digest_sha256": str(header["evaluation_request_digest_sha256"]),
        "verifier_image_digest_sha256": str(header["verifier_image_digest_sha256"]),
    }

    def reject(reward: float = 0.0) -> int:
        return emit("rejected", reward, bound)

    container = header.get("container")
    duration = container.get("duration_seconds") if isinstance(container, dict) else None
    if (
        not isinstance(container, dict)
        or set(container) != CONTAINER_KEYS
        or container.get("state") != "exited"
        or container.get("exit_code") != 0
        or container.get("oom_killed") is not False
        or container.get("timed_out") is not False
        or container.get("overflowed") is not False
        or container.get("removed") is not True
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        return reject()
    stdout_info = header.get("stdout")
    stderr_info = header.get("stderr")
    if (
        not isinstance(stdout_info, dict)
        or not isinstance(stderr_info, dict)
        or set(stdout_info) != IO_KEYS
        or set(stderr_info) != IO_KEYS
        or stdout_info.get("authority") != "untrusted"
        or stderr_info.get("authority") != "untrusted"
    ):
        return reject()
    stdout_len = stdout_info.get("byte_count")
    stderr_len = stderr_info.get("byte_count")
    if (
        isinstance(stdout_len, bool)
        or isinstance(stderr_len, bool)
        or not isinstance(stdout_len, int)
        or not isinstance(stderr_len, int)
        or stdout_len < 0
        or stderr_len < 0
    ):
        return reject()
    stdout_start = header_end
    stdout_end = stdout_start + stdout_len
    stderr_end = stdout_end + stderr_len
    if stderr_end != len(raw):
        return reject()
    stdout_bytes = raw[stdout_start:stdout_end]
    stderr_bytes = raw[stdout_end:stderr_end]
    if (
        hashlib.sha256(stdout_bytes).hexdigest() != stdout_info.get("digest_sha256")
        or hashlib.sha256(stderr_bytes).hexdigest() != stderr_info.get("digest_sha256")
        or stderr_bytes
    ):
        return reject()
    try:
        snapshot = json.loads(
            stdout_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return reject()
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != SNAPSHOT_KEYS
        or stdout_bytes != json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        or snapshot.get("schema_version") != "rolebench.code-flowchart-runner-snapshot/v1"
        or snapshot.get("status") != "executed"
        or snapshot.get("error") is not None
    ):
        return reject()
    if snapshot.get("flowchart_sha256") != EXPECTED_FLOWCHART_SHA256:
        return reject()
    submission = snapshot.get("submission")
    passed, reward, _ = _score_submission(submission)
    if not passed:
        return reject(reward=reward)
    return emit("accepted", 1.0, bound)


if __name__ == "__main__":
    raise SystemExit(main())
