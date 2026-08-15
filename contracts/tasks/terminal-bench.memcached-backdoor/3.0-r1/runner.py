#!/usr/bin/env python3
"""Strict citation-aware parser for the authentication review task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 16 * 1024
SCHEMA_VERSION = "rolebench.security-review/v1"
SNAPSHOT_VERSION = "rolebench.security-review-runner-snapshot/v1"
TOP_KEYS = {"schema_version", "backdoor_present", "findings", "reviewed_safe"}
FINDING_KEYS = {"function", "address", "defect", "severity", "impact", "evidence", "remediation"}
FUNCTION = re.compile(r"FUNCTION ([a-z_][a-z0-9_]*) @ (0x[0-9a-f]+)")
LABEL = re.compile(r"([A-Z][0-9]{2}):")
CITATION = re.compile(r"decompilation\.txt:([A-Z][0-9]{2})-([A-Z][0-9]{2})")
ADDRESS = re.compile(r"0x[0-9a-f]+")


class SubmissionError(ValueError):
    """Malformed review artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_bundle() -> tuple[str, dict[str, str], set[str]]:
    path = Path("/opt/rolebench/task/public/workspace/decompilation.txt")
    if not path.is_file():
        path = Path(__file__).resolve().parent / "public" / "workspace" / "decompilation.txt"
    text = path.read_text(encoding="utf-8")
    functions = {name: address for name, address in FUNCTION.findall(text)}
    labels = set(LABEL.findall(text))
    if not functions or not labels:
        raise SubmissionError("review bundle is malformed")
    return text, functions, labels


def _validate(value: object, functions: dict[str, str], labels: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != TOP_KEYS:
        raise SubmissionError("submission has unexpected keys")
    if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("backdoor_present"), bool):
        raise SubmissionError("submission schema or backdoor_present is invalid")
    findings = value.get("findings")
    reviewed_safe = value.get("reviewed_safe")
    if not isinstance(findings, list) or not 0 < len(findings) <= len(functions):
        raise SubmissionError("findings must be a non-empty bounded array")
    if (
        not isinstance(reviewed_safe, list)
        or any(not isinstance(item, str) or item not in functions for item in reviewed_safe)
        or len(set(reviewed_safe)) != len(reviewed_safe)
    ):
        raise SubmissionError("reviewed_safe contains an unknown or duplicate function")

    finding_functions: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != FINDING_KEYS:
            raise SubmissionError("finding has unexpected keys")
        name = finding.get("function")
        address = finding.get("address")
        if not isinstance(name, str) or name not in functions or name in finding_functions:
            raise SubmissionError("finding function is unknown or duplicated")
        if not isinstance(address, str) or ADDRESS.fullmatch(address) is None or functions[name] != address:
            raise SubmissionError("finding address does not match the review bundle")
        finding_functions.add(name)
        for field in ("defect", "impact", "remediation"):
            if not isinstance(finding.get(field), str) or not finding[field]:
                raise SubmissionError(f"{field} must be a non-empty string")
        if finding.get("severity") not in {"low", "medium", "high", "critical"}:
            raise SubmissionError("severity is invalid")
        evidence = finding.get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4 or len(set(evidence)) != len(evidence):
            raise SubmissionError("evidence must contain one to four unique citations")
        for citation in evidence:
            match = CITATION.fullmatch(citation) if isinstance(citation, str) else None
            if match is None or match.group(1) not in labels or match.group(2) not in labels:
                raise SubmissionError("evidence citation does not resolve")
    if finding_functions & set(reviewed_safe) or finding_functions | set(reviewed_safe) != set(functions):
        raise SubmissionError("each function must be classified exactly once")
    return value


def _snapshot(*, status: str, error: str | None, bundle_sha256: str | None, submission: object) -> str:
    return json.dumps(
        {
            "schema_version": SNAPSHOT_VERSION,
            "status": status,
            "error": error,
            "bundle_sha256": bundle_sha256,
            "submission": submission,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 16 KiB", bundle_sha256=None, submission=None))
        return 0
    try:
        bundle, functions, labels = _load_bundle()
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        submission = _validate(parsed, functions, labels)
        bundle_sha256 = hashlib.sha256(bundle.encode("utf-8")).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, OSError, TypeError, RecursionError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), bundle_sha256=None, submission=None))
        return 0
    sys.stdout.write(_snapshot(status="executed", error=None, bundle_sha256=bundle_sha256, submission=submission))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
