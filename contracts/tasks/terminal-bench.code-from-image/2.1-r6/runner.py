#!/usr/bin/env python3
"""Strict artifact parser for the image-grounded code task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 4 * 1024
SCHEMA_VERSION = "rolebench.image-code-answer/v1"
SNAPSHOT_VERSION = "rolebench.image-code-runner-snapshot/v1"
SUBMISSION_KEYS = {"schema_version", "salt", "slice_start", "slice_end", "digest_sha256"}
HEX = re.compile(r"[0-9a-f]{64}")
SALT = re.compile(r"[A-Z0-9-]{4,32}")


class SubmissionError(ValueError):
    """Malformed candidate artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _bounded_int(value: str) -> int:
    if len(value) > 3:
        raise SubmissionError("integer exceeds three digits")
    return int(value)


def _snapshot(*, status: str, error: str | None, asset_sha256: str | None, submission: object) -> str:
    return json.dumps(
        {
            "schema_version": SNAPSHOT_VERSION,
            "status": status,
            "error": error,
            "asset_sha256": asset_sha256,
            "submission": submission,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != SUBMISSION_KEYS:
        raise SubmissionError("submission has unexpected keys")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise SubmissionError("unsupported submission schema")
    salt = value.get("salt")
    start = value.get("slice_start")
    end = value.get("slice_end")
    digest = value.get("digest_sha256")
    if not isinstance(salt, str) or SALT.fullmatch(salt) is None:
        raise SubmissionError("salt must be 4 to 32 uppercase ASCII letters, digits, or hyphens")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start < end <= 32
    ):
        raise SubmissionError("slice bounds must satisfy 0 <= start < end <= 32")
    if not isinstance(digest, str) or HEX.fullmatch(digest) is None:
        raise SubmissionError("digest_sha256 must be lowercase SHA-256")
    return value


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 4 KiB", asset_sha256=None, submission=None))
        return 0
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_int=_bounded_int,
        )
        submission = _validate(parsed)
        image_path = Path("/opt/rolebench/task/public/workspace/code.png")
        if not image_path.is_file():
            image_path = Path(__file__).resolve().parent / "public" / "workspace" / "code.png"
        asset_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, OSError, TypeError, RecursionError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), asset_sha256=None, submission=None))
        return 0
    sys.stdout.write(_snapshot(status="executed", error=None, asset_sha256=asset_sha256, submission=submission))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
