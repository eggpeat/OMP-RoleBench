#!/usr/bin/env python3
"""Strict data-only runner for the db-wal-recovery submission."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES = 64 * 1024
TOP_LEVEL_KEYS = {
    "schema_version",
    "diagnosis",
    "hypothesis_testing",
    "selected_transform",
    "recovery_verification",
    "recovered_records",
}
DIAGNOSIS_KEYS = {
    "sidecar_file",
    "observed_magic_hex",
    "expected_magic_hex",
    "corruption_mechanism",
}
HYPOTHESIS_KEYS = {
    "hypothesis_id",
    "transform_kind",
    "parameter_int",
    "resulting_magic_hex",
    "wal_magic_valid",
}
TRANSFORM_KEYS = {
    "transform_kind",
    "parameter_int",
    "target_magic_hex",
}
VERIFICATION_KEYS = {
    "base_record_count",
    "recovered_record_count",
    "checkpoint_applied",
}
RECORD_KEYS = {"id", "name", "value"}

HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{8}$")


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


def _validate_submission(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SubmissionError("submission must be a top-level JSON object")
    if set(value) != TOP_LEVEL_KEYS:
        raise SubmissionError(f"submission keys must match exact top-level keys: {sorted(TOP_LEVEL_KEYS)}")

    if value.get("schema_version") != "rolebench.db-wal-recovery-submission/v1":
        raise SubmissionError("invalid schema_version")

    # 1. Diagnosis
    diag = value.get("diagnosis")
    if not isinstance(diag, dict) or set(diag) != DIAGNOSIS_KEYS:
        raise SubmissionError("diagnosis object has invalid or missing keys")
    if diag.get("sidecar_file") != "main.db-wal":
        raise SubmissionError("diagnosis.sidecar_file must be 'main.db-wal'")
    obs_hex = diag.get("observed_magic_hex")
    if not isinstance(obs_hex, str) or HEX_PATTERN.fullmatch(obs_hex) is None:
        raise SubmissionError("diagnosis.observed_magic_hex must be an 8-character hex string")
    exp_hex = diag.get("expected_magic_hex")
    if not isinstance(exp_hex, str) or HEX_PATTERN.fullmatch(exp_hex) is None:
        raise SubmissionError("diagnosis.expected_magic_hex must be an 8-character hex string")
    mech = diag.get("corruption_mechanism")
    if not isinstance(mech, str) or not mech or len(mech) > 64:
        raise SubmissionError("diagnosis.corruption_mechanism must be a bounded non-empty string")

    # 2. Hypothesis testing
    hypotheses = value.get("hypothesis_testing")
    if not isinstance(hypotheses, list) or len(hypotheses) < 1 or len(hypotheses) > 20:
        raise SubmissionError("hypothesis_testing must be an array of 1 to 20 items")
    seen_hyp_ids: set[str] = set()
    normalized_hypotheses: list[dict[str, object]] = []
    for hyp in hypotheses:
        if not isinstance(hyp, dict) or set(hyp) != HYPOTHESIS_KEYS:
            raise SubmissionError("hypothesis item has invalid or missing keys")
        hyp_id = hyp.get("hypothesis_id")
        if not isinstance(hyp_id, str) or not hyp_id or len(hyp_id) > 64:
            raise SubmissionError("hypothesis_id must be a non-empty bounded string")
        if hyp_id in seen_hyp_ids:
            raise SubmissionError(f"duplicate hypothesis_id: {hyp_id}")
        seen_hyp_ids.add(hyp_id)

        t_kind = hyp.get("transform_kind")
        if not isinstance(t_kind, str) or not t_kind or len(t_kind) > 64:
            raise SubmissionError("transform_kind must be a non-empty bounded string")

        param = hyp.get("parameter_int")
        if isinstance(param, bool) or not isinstance(param, int) or not (-1_000_000 <= param <= 1_000_000):
            raise SubmissionError("parameter_int must be an integer between -1,000,000 and 1,000,000")

        res_hex = hyp.get("resulting_magic_hex")
        if not isinstance(res_hex, str) or HEX_PATTERN.fullmatch(res_hex) is None:
            raise SubmissionError("resulting_magic_hex must be an 8-character hex string")

        wal_valid = hyp.get("wal_magic_valid")
        if not isinstance(wal_valid, bool):
            raise SubmissionError("wal_magic_valid must be a boolean")

        normalized_hypotheses.append({
            "hypothesis_id": hyp_id,
            "transform_kind": t_kind,
            "parameter_int": param,
            "resulting_magic_hex": res_hex.lower(),
            "wal_magic_valid": wal_valid,
        })

    # 3. Selected transform
    sel = value.get("selected_transform")
    if not isinstance(sel, dict) or set(sel) != TRANSFORM_KEYS:
        raise SubmissionError("selected_transform has invalid or missing keys")
    sel_kind = sel.get("transform_kind")
    if not isinstance(sel_kind, str) or not sel_kind or len(sel_kind) > 64:
        raise SubmissionError("selected_transform.transform_kind must be a non-empty bounded string")
    sel_param = sel.get("parameter_int")
    if isinstance(sel_param, bool) or not isinstance(sel_param, int) or not (-1_000_000 <= sel_param <= 1_000_000):
        raise SubmissionError("selected_transform.parameter_int must be an integer between -1,000,000 and 1,000,000")
    tgt_hex = sel.get("target_magic_hex")
    if not isinstance(tgt_hex, str) or HEX_PATTERN.fullmatch(tgt_hex) is None:
        raise SubmissionError("selected_transform.target_magic_hex must be an 8-character hex string")

    # 4. Recovery verification
    rec_ver = value.get("recovery_verification")
    if not isinstance(rec_ver, dict) or set(rec_ver) != VERIFICATION_KEYS:
        raise SubmissionError("recovery_verification has invalid or missing keys")
    base_cnt = rec_ver.get("base_record_count")
    if isinstance(base_cnt, bool) or not isinstance(base_cnt, int) or base_cnt < 0 or base_cnt > 1000:
        raise SubmissionError("base_record_count must be an integer between 0 and 1000")
    rec_cnt = rec_ver.get("recovered_record_count")
    if isinstance(rec_cnt, bool) or not isinstance(rec_cnt, int) or rec_cnt < 0 or rec_cnt > 1000:
        raise SubmissionError("recovered_record_count must be an integer between 0 and 1000")
    chk = rec_ver.get("checkpoint_applied")
    if not isinstance(chk, bool):
        raise SubmissionError("checkpoint_applied must be a boolean")

    # 5. Recovered records
    records = value.get("recovered_records")
    if not isinstance(records, list) or len(records) != 11:
        raise SubmissionError(f"expected 11 recovered records, got {len(records) if isinstance(records, list) else 'non-list'}")

    seen_ids: list[int] = []
    normalized_records: list[dict[str, object]] = []
    for item in records:
        if not isinstance(item, dict) or set(item) != RECORD_KEYS:
            raise SubmissionError("each record must have exact keys: id, name, value")

        rec_id = item.get("id")
        if isinstance(rec_id, bool) or not isinstance(rec_id, int) or rec_id < 1 or rec_id > 1000:
            raise SubmissionError("id must be an integer between 1 and 1000")

        name = item.get("name")
        if not isinstance(name, str) or not name or len(name) > 256 or "\0" in name:
            raise SubmissionError("name must be a bounded non-empty string")

        val = item.get("value")
        if isinstance(val, bool) or not isinstance(val, int) or val < 0 or val > 1_000_000:
            raise SubmissionError("value must be an integer between 0 and 1,000,000")

        seen_ids.append(rec_id)
        normalized_records.append({
            "id": rec_id,
            "name": name,
            "value": val,
        })

    if seen_ids != sorted(seen_ids):
        raise SubmissionError("recovered records must be sorted by id ascending")
    if len(seen_ids) != len(set(seen_ids)):
        raise SubmissionError("duplicate id in recovered records")
    if seen_ids != list(range(1, 12)):
        raise SubmissionError("expected contiguous records with id 1 through 11")

    return {
        "schema_version": "rolebench.db-wal-recovery-submission/v1",
        "diagnosis": {
            "sidecar_file": "main.db-wal",
            "observed_magic_hex": obs_hex.lower(),
            "expected_magic_hex": exp_hex.lower(),
            "corruption_mechanism": mech,
        },
        "hypothesis_testing": normalized_hypotheses,
        "selected_transform": {
            "transform_kind": sel_kind,
            "parameter_int": sel_param,
            "target_magic_hex": tgt_hex.lower(),
        },
        "recovery_verification": {
            "base_record_count": base_cnt,
            "recovered_record_count": rec_cnt,
            "checkpoint_applied": chk,
        },
        "recovered_records": normalized_records,
    }


def _snapshot(*, status: str, error: str | None, submission: object) -> str:
    return json.dumps(
        {
            "schema_version": "rolebench.db-wal-recovery-runner-snapshot/v1",
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
    if not payload:
        sys.stdout.write(_snapshot(status="rejected", error="empty artifact payload", submission=None))
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
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError) as exc:
        sys.stdout.write(_snapshot(status="rejected", error=str(exc), submission=None))
        return 0

    sys.stdout.write(_snapshot(status="parsed", error=None, submission=submission))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
