#!/usr/bin/env python3
"""Strict structural parser for the image-grounded flowchart graph task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 32 * 1024
SCHEMA_VERSION = "rolebench.code-flowchart-graph/v1"
SNAPSHOT_VERSION = "rolebench.code-flowchart-runner-snapshot/v1"
TOP_KEYS = {"schema_version", "pipeline_name", "lanes", "nodes", "edges"}
NODE_KEYS = {"id", "kind", "lane", "label"}
EDGE_KEYS = {"source", "target", "condition"}
VALID_KINDS = {"start", "decision", "process", "terminal"}
IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


class SubmissionError(ValueError):
    """Malformed flowchart-graph artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _validate(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != TOP_KEYS:
        raise SubmissionError("submission has unexpected top-level keys")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise SubmissionError("unsupported submission schema_version")
    if value.get("pipeline_name") != "request-routing-pipeline":
        raise SubmissionError("invalid pipeline_name")

    lanes = value.get("lanes")
    nodes = value.get("nodes")
    edges = value.get("edges")

    if not isinstance(lanes, list) or not (1 <= len(lanes) <= 16):
        raise SubmissionError("lanes must be a non-empty list of at most 16 strings")
    for lane in lanes:
        if not isinstance(lane, str) or not lane or len(lane) > 64:
            raise SubmissionError("invalid lane name")

    if not isinstance(nodes, list) or not (1 <= len(nodes) <= 64):
        raise SubmissionError("nodes must be a list of 1 to 64 items")

    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) != NODE_KEYS:
            raise SubmissionError("node has invalid keys")
        nid = node.get("id")
        kind = node.get("kind")
        lane = node.get("lane")
        label = node.get("label")
        if not isinstance(nid, str) or IDENTIFIER.fullmatch(nid) is None or nid in node_ids:
            raise SubmissionError("invalid or duplicated node id")
        if kind not in VALID_KINDS:
            raise SubmissionError("invalid node kind")
        if not isinstance(lane, str) or lane not in lanes:
            raise SubmissionError("node lane must refer to a declared lane")
        if not isinstance(label, str) or not label or len(label) > 512:
            raise SubmissionError("invalid node label")
        node_ids.add(nid)

    if not isinstance(edges, list) or not (1 <= len(edges) <= 128):
        raise SubmissionError("edges must be a list of 1 to 128 items")

    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != EDGE_KEYS:
            raise SubmissionError("edge has invalid keys")
        source = edge.get("source")
        target = edge.get("target")
        condition = edge.get("condition")
        if not isinstance(source, str) or source not in node_ids:
            raise SubmissionError("edge source must refer to a valid node id")
        if not isinstance(target, str) or target not in node_ids:
            raise SubmissionError("edge target must refer to a valid node id")
        if not isinstance(condition, str) or not condition or len(condition) > 64:
            raise SubmissionError("invalid edge condition")

    return value


def _snapshot(*, status: str, error: str | None, flowchart_sha256: str | None, submission: object) -> str:
    return json.dumps(
        {
            "schema_version": SNAPSHOT_VERSION,
            "status": status,
            "error": error,
            "flowchart_sha256": flowchart_sha256,
            "submission": submission,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 32 KiB", flowchart_sha256=None, submission=None))
        return 0
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        submission = _validate(parsed)
        image = Path("/opt/rolebench/task/public/workspace/flowchart.png")
        if not image.is_file():
            image = Path(__file__).resolve().parent / "public" / "workspace" / "flowchart.png"
        flowchart_sha256 = hashlib.sha256(image.read_bytes()).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, OSError, TypeError, RecursionError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), flowchart_sha256=None, submission=None))
        return 0
    sys.stdout.write(_snapshot(status="executed", error=None, flowchart_sha256=flowchart_sha256, submission=submission))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
