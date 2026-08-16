#!/usr/bin/env python3
"""Runner parser for structured code review defect recall task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 64 * 1024
SCHEMA_VERSION = "rolebench.review-findings/v1"
SNAPSHOT_VERSION = "rolebench.review-findings-runner-snapshot/v1"
ALLOWED_CATEGORIES = {"concurrency", "resource-leak", "security", "correctness", "performance"}
ALLOWED_SEVERITIES = {"high", "medium", "low"}
ALLOWED_VERDICTS = {"approved", "changes_requested"}


class SubmissionError(ValueError):
    """Malformed review finding artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_patch() -> tuple[bytes, str]:
    path = Path("/opt/rolebench/task/public/workspace/change.patch")
    if not path.exists():
        path = Path("public/workspace/change.patch")
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def _validate(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SubmissionError("submission must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise SubmissionError(f"invalid schema_version {value.get('schema_version')!r}")
    verdict = value.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        raise SubmissionError(f"invalid verdict {verdict!r}")
    findings = value.get("findings")
    if not isinstance(findings, list):
        raise SubmissionError("findings must be a list")

    for idx, item in enumerate(findings):
        if not isinstance(item, dict):
            raise SubmissionError(f"finding {idx} must be an object")
        file_path = item.get("file")
        if not isinstance(file_path, str) or not file_path:
            raise SubmissionError(f"finding {idx} missing valid file")
        line_start = item.get("line_start")
        line_end = item.get("line_end")
        if not isinstance(line_start, int) or not isinstance(line_end, int) or line_start < 1 or line_end < line_start:
            raise SubmissionError(f"finding {idx} invalid line range ({line_start}, {line_end})")
        severity = item.get("severity")
        if severity not in ALLOWED_SEVERITIES:
            raise SubmissionError(f"finding {idx} invalid severity {severity!r}")
        category = item.get("category")
        if category not in ALLOWED_CATEGORIES:
            raise SubmissionError(f"finding {idx} invalid category {category!r}")
        desc = item.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise SubmissionError(f"finding {idx} missing description")

    return value


def _snapshot(*, status: str, error: str | None, patch_sha256: str | None, submission: object) -> str:
    return json.dumps(
        {
            "schema_version": SNAPSHOT_VERSION,
            "status": status,
            "error": error,
            "patch_sha256": patch_sha256,
            "submission": submission,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        snapshot = _snapshot(status="rejected", error="artifact exceeds size limit", patch_sha256=None, submission=None)
    else:
        try:
            _, patch_sha = _load_patch()
            parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            validated = _validate(parsed)
            snapshot = _snapshot(status="accepted", error=None, patch_sha256=patch_sha, submission=validated)
        except Exception as err:
            snapshot = _snapshot(status="rejected", error=str(err), patch_sha256=None, submission=None)

    raw_bytes = snapshot.encode("utf-8")
    header = f"OMP-RUNNER-EVIDENCE-V1\n".encode("ascii")
    length_prefix = f"{len(raw_bytes)}\n".encode("ascii")
    sys.stdout.buffer.write(header + length_prefix + raw_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
