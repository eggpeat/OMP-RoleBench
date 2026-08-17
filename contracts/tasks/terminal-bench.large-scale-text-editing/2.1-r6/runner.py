#!/usr/bin/env python3
"""Bounded text-editing runner for multi-file mechanical refactoring."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 64 * 1024
MAX_FILE_BYTES = 32 * 1024
SCHEMA_VERSION = "rolebench.text-editing-submission/v1"
SNAPSHOT_VERSION = "rolebench.text-editing-runner-snapshot/v1"
ALLOWED_TOP_KEYS = frozenset({"schema_version", "modified_files", "files"})


class SubmissionError(ValueError):
    """Malformed or invalid candidate artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _validate_submission(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        raise SubmissionError("submission must be a JSON object")
    keys = set(data.keys())
    if not keys.issubset(ALLOWED_TOP_KEYS) or "schema_version" not in keys:
        raise SubmissionError("submission contains invalid or missing top-level keys")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SubmissionError(f"schema_version must be {SCHEMA_VERSION!r}")

    files_obj = data.get("modified_files")
    if files_obj is None and "files" in data:
        files_obj = data.get("files")
    if not isinstance(files_obj, dict):
        raise SubmissionError("modified_files must be an object mapping file paths to file contents")

    normalized_files: dict[str, str] = {}
    for path_key, content_val in files_obj.items():
        if not isinstance(path_key, str) or not path_key.strip():
            raise SubmissionError("file path keys must be non-empty strings")
        clean_path = path_key.strip().replace("\\", "/")
        if clean_path.startswith("/") or ".." in clean_path.split("/"):
            raise SubmissionError(f"invalid relative file path {path_key!r}")
        if not isinstance(content_val, str):
            raise SubmissionError(f"file content for {path_key!r} must be a string")
        if len(content_val.encode("utf-8")) > MAX_FILE_BYTES:
            raise SubmissionError(f"file content for {path_key!r} exceeds maximum allowed size")
        normalized_files[clean_path] = content_val

    return {
        "schema_version": SCHEMA_VERSION,
        "modified_files": normalized_files,
    }


def _snapshot(*, status: str, error: str | None, submission: object) -> str:
    return json.dumps(
        {
            "schema_version": SNAPSHOT_VERSION,
            "status": status,
            "error": error,
            "submission": submission,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(
            _snapshot(status="rejected", error="artifact exceeds 64 KiB", submission=None)
        )
        return 0

    try:
        raw_text = payload.decode("utf-8")
        parsed = json.loads(
            raw_text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        validated = _validate_submission(parsed)
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, TypeError) as exc:
        sys.stdout.write(
            _snapshot(status="rejected", error=str(exc), submission=None)
        )
        return 0

    sys.stdout.write(
        _snapshot(status="executed", error=None, submission=validated)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
