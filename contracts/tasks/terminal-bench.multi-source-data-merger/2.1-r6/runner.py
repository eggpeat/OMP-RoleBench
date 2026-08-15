#!/usr/bin/env python3
"""Strict data-only runner for the multi-source merger submission."""

from __future__ import annotations

import json
import re
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 64 * 1024
TOP_LEVEL_KEYS = {"schema_version", "merged_users", "conflict_report"}
USER_KEYS = {"user_id", "name", "email", "created_date", "status"}
REPORT_KEYS = {"total_conflicts", "conflicts"}
CONFLICT_KEYS = {"user_id", "field", "values", "selected"}
SOURCE_KEYS = {"source_a", "source_b", "source_c"}
FIELD_ORDER = {"name": 0, "email": 1, "created_date": 2, "status": 3}
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class SubmissionError(ValueError):
    """A malformed candidate artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _bounded_int(value: str) -> int:
    if len(value) > 20:
        raise SubmissionError("JSON integer exceeds 20 digits")
    return int(value)


def _reject_float(value: str) -> NoReturn:
    raise SubmissionError(f"JSON floating-point value {value!r} is not allowed")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _validate_json_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 10_000:
            raise SubmissionError("JSON document exceeds 10000 nodes")
        if depth > 64:
            raise SubmissionError("JSON document exceeds depth 64")
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise SubmissionError(f"{field} must be a non-empty bounded string")
    return value


def _user_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SubmissionError("user_id must be a positive integer")
    return value


def _validate_submission(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != TOP_LEVEL_KEYS:
        raise SubmissionError("submission has invalid top-level keys")
    if value.get("schema_version") != "rolebench.multi-source-submission/v1":
        raise SubmissionError("submission schema version is invalid")

    users = value.get("merged_users")
    if not isinstance(users, list) or len(users) > 100:
        raise SubmissionError("merged_users must be a bounded array")
    user_ids: list[int] = []
    for item in users:
        if not isinstance(item, dict) or set(item) != USER_KEYS:
            raise SubmissionError("merged user has invalid keys")
        user_id = _user_id(item.get("user_id"))
        user_ids.append(user_id)
        _text(item.get("name"), field="name")
        email = _text(item.get("email"), field="email")
        if "@" not in email:
            raise SubmissionError("email must contain @")
        created_date = _text(item.get("created_date"), field="created_date")
        if DATE_PATTERN.fullmatch(created_date) is None:
            raise SubmissionError("created_date must use YYYY-MM-DD")
        status = item.get("status")
        if not isinstance(status, str) or status not in {"active", "inactive"}:
            raise SubmissionError("status must be active or inactive")
    if user_ids != sorted(user_ids) or len(user_ids) != len(set(user_ids)):
        raise SubmissionError("merged users must have unique ascending user_id values")

    report = value.get("conflict_report")
    if not isinstance(report, dict) or set(report) != REPORT_KEYS:
        raise SubmissionError("conflict_report has invalid keys")
    conflicts = report.get("conflicts")
    total = report.get("total_conflicts")
    if (
        not isinstance(conflicts, list)
        or len(conflicts) > 400
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total != len(conflicts)
    ):
        raise SubmissionError("conflict count is invalid")

    conflict_order: list[tuple[int, int]] = []
    conflict_ids: set[tuple[int, str]] = set()
    for item in conflicts:
        if not isinstance(item, dict) or set(item) != CONFLICT_KEYS:
            raise SubmissionError("conflict has invalid keys")
        user_id = _user_id(item.get("user_id"))
        field = item.get("field")
        if not isinstance(field, str) or field not in FIELD_ORDER:
            raise SubmissionError("conflict field is invalid")
        identity = (user_id, field)
        if identity in conflict_ids:
            raise SubmissionError("duplicate conflict")
        conflict_ids.add(identity)
        conflict_order.append((user_id, FIELD_ORDER[field]))
        values = item.get("values")
        if (
            not isinstance(values, dict)
            or not 2 <= len(values) <= 3
            or not set(values).issubset(SOURCE_KEYS)
        ):
            raise SubmissionError("conflict values are invalid")
        normalized_values = {
            source: _text(source_value, field=f"values.{source}")
            for source, source_value in values.items()
        }
        selected = _text(item.get("selected"), field="selected")
        if selected not in normalized_values.values() or len(set(normalized_values.values())) < 2:
            raise SubmissionError("conflict selection or differing values are invalid")
    if conflict_order != sorted(conflict_order):
        raise SubmissionError("conflicts must use the required deterministic order")

    return value


def _snapshot(*, status: str, error: str | None, submission: object) -> str:
    return json.dumps(
        {
            "schema_version": "rolebench.multi-source-runner-snapshot/v1",
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
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 64 KiB", submission=None))
        return 0
    try:
        text = payload.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
            parse_int=_bounded_int,
        )
        _validate_json_shape(parsed)
        submission = _validate_submission(parsed)
    except (UnicodeDecodeError, ValueError, TypeError, OverflowError, RecursionError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), submission=None))
        return 0
    sys.stdout.write(_snapshot(status="parsed", error=None, submission=submission))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
