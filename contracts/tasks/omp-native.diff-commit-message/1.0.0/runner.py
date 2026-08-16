#!/usr/bin/env python3
"""Convention and citation parser for the diff-to-commit task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 8 * 1024
SCHEMA_VERSION = "rolebench.commit-message/v1"
SNAPSHOT_VERSION = "rolebench.commit-message-runner-snapshot/v1"
KEYS = {"schema_version", "type", "scope", "subject", "body", "evidence", "breaking"}
CITATION = re.compile(r"change\.patch:([1-9][0-9]*)-([1-9][0-9]*)")
SUBJECT = re.compile(r"[a-z0-9][a-z0-9 ._-]*[a-z0-9]")
TOKEN = re.compile(r"[a-z][a-z0-9_-]*")


class SubmissionError(ValueError):
    """Malformed commit artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_patch() -> tuple[bytes, int]:
    path = Path("/opt/rolebench/task/public/workspace/change.patch")
    if not path.is_file():
        path = Path(__file__).resolve().parent / "public" / "workspace" / "change.patch"
    data = path.read_bytes()
    return data, len(data.decode("utf-8").splitlines())


def _validate(value: object, patch_lines: int) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != KEYS:
        raise SubmissionError("submission has unexpected keys")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("type") not in {"fix", "feat", "refactor", "test", "docs"}:
        raise SubmissionError("submission schema or type is invalid")
    if not isinstance(value.get("scope"), str) or TOKEN.fullmatch(value["scope"]) is None:
        raise SubmissionError("scope is invalid")
    subject = value.get("subject")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 72 or SUBJECT.fullmatch(subject) is None:
        raise SubmissionError("subject violates the commit convention")
    body = value.get("body")
    evidence = value.get("evidence")
    if not isinstance(body, list) or not 1 <= len(body) <= 6 or any(not isinstance(item, str) or not item.endswith(".") or len(item) > 160 for item in body):
        raise SubmissionError("body must contain one to six concise sentences")
    if not isinstance(evidence, list) or len(evidence) != len(body) or len(set(evidence)) != len(evidence):
        raise SubmissionError("evidence must contain one unique citation per body sentence")
    previous_end = 0
    for citation in evidence:
        match = CITATION.fullmatch(citation) if isinstance(citation, str) else None
        if match is None:
            raise SubmissionError("evidence citation is malformed")
        start, end = (int(part) for part in match.groups())
        if not previous_end < start <= end <= patch_lines:
            raise SubmissionError("evidence ranges must resolve and follow diff order")
        previous_end = end
    if not isinstance(value.get("breaking"), bool):
        raise SubmissionError("breaking must be a boolean")
    return value


def _snapshot(*, status: str, error: str | None, patch_sha256: str | None, submission: object) -> str:
    return json.dumps({"schema_version": SNAPSHOT_VERSION, "status": status, "error": error, "patch_sha256": patch_sha256, "submission": submission}, sort_keys=True, separators=(",", ":"))


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 8 KiB", patch_sha256=None, submission=None))
        return 0
    try:
        patch, patch_lines = _load_patch()
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        submission = _validate(parsed, patch_lines)
        patch_sha256 = hashlib.sha256(patch).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, OSError, TypeError, RecursionError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), patch_sha256=None, submission=None))
        return 0
    sys.stdout.write(_snapshot(status="executed", error=None, patch_sha256=patch_sha256, submission=submission))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
