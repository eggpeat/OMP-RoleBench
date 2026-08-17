#!/usr/bin/env python3
"""Exact-format parser for the tiny metadata-normalization task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 16 * 1024
SCHEMA_VERSION = "rolebench.metadata-normalization/v1"
SNAPSHOT_VERSION = "rolebench.metadata-normalization-runner-snapshot/v1"
TOP_KEYS = ["schema_version", "records"]
RECORD_KEYS = ["run_id", "outcome", "role", "retryable", "latency_bucket", "labels"]


class SubmissionError(ValueError):
    """Malformed normalized metadata artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _validate(value: object, payload: bytes) -> dict[str, object]:
    if not isinstance(value, dict) or list(value) != TOP_KEYS:
        raise SubmissionError("submission keys or key order are invalid")
    if payload != json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"):
        raise SubmissionError("submission must be compact canonical JSON without surrounding whitespace")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise SubmissionError("unsupported submission schema")
    records = value.get("records")
    if not isinstance(records, list) or not records:
        raise SubmissionError("records must be a non-empty list")
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict) or list(rec) != RECORD_KEYS:
            raise SubmissionError(f"record {idx} keys or key order are invalid")
        if any(not isinstance(rec.get(field), str) or not rec[field] for field in ("run_id", "outcome", "role", "latency_bucket")):
            raise SubmissionError(f"record {idx} normalized string fields must be non-empty")
        if rec.get("outcome") not in ("passed", "failed"):
            raise SubmissionError(f"record {idx} outcome must be 'passed' or 'failed'")
        if not isinstance(rec.get("retryable"), bool):
            raise SubmissionError(f"record {idx} retryable must be a boolean")
        if rec.get("latency_bucket") not in ("under-100ms", "100-999ms", "at-least-1000ms"):
            raise SubmissionError(f"record {idx} latency_bucket is invalid")
        labels = rec.get("labels")
        if not isinstance(labels, list) or any(not isinstance(item, str) or not item for item in labels) or len(labels) != len(set(labels)):
            raise SubmissionError(f"record {idx} labels must be unique non-empty strings")
    return value


def _snapshot(*, status: str, error: str | None, fixture_sha256: str | None, submission: object) -> str:
    return json.dumps({"schema_version": SNAPSHOT_VERSION, "status": status, "error": error, "fixture_sha256": fixture_sha256, "submission": submission}, sort_keys=True, separators=(",", ":"))


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 16 KiB", fixture_sha256=None, submission=None))
        return 0
    try:
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        submission = _validate(parsed, payload)
        fixture = Path("/opt/rolebench/task/public/workspace/route_header.txt")
        if not fixture.is_file():
            fixture = Path(__file__).resolve().parent / "public" / "workspace" / "route_header.txt"
        fixture_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, OSError, TypeError, RecursionError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), fixture_sha256=None, submission=None))
        return 0
    sys.stdout.write(_snapshot(status="executed", error=None, fixture_sha256=fixture_sha256, submission=submission))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
