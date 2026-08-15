#!/usr/bin/env python3
"""Strict data-only runner for custom-memory-heap-crash categorical diagnostic submissions."""

from __future__ import annotations

import json
import math
import sys
from typing import NoReturn

MAX_ARTIFACT_BYTES: int = 64 * 1024  # 64 KiB

TOP_LEVEL_KEYS: set[str] = {
    "schema_version",
    "fault_summary",
    "primary_hypothesis_id",
    "fault_category",
    "trigger_id",
    "failure_point_id",
    "remediation_action_id",
    "cited_evidence_ids",
    "hypotheses_ranking",
    "technical_notes",
}

VALID_PRIMARY_HYPOTHESES: set[str] = {
    "HYP_CUSTOM_HEAP_LOCALE_DESTRUCTION",
    "HYP_MONTE_CARLO_STACK_CORRUPTION",
    "HYP_COMPILER_INLINING_REORDERING",
    "HYP_STATIC_MUTEX_DEADLOCK",
}

VALID_FAULT_CATEGORIES: set[str] = {
    "ALLOCATOR_LIFETIME_MISMATCH",
    "BUFFER_OVERFLOW_STACK",
    "UNALIGNED_MEMORY_ACCESS",
    "DOUBLE_FREE_DURING_CLEANUP",
    "RECURSIVE_INITIALIZATION_DEADLOCK",
}

VALID_TRIGGERS: set[str] = {
    "STREAM_OUTPUT_FORMATTING",
    "RANDOM_NUMBER_GENERATOR_INIT",
    "MATH_LIBRARY_LINK_ORDER",
    "OPTIMIZATION_LEVEL_INLINING",
}

VALID_FAILURE_POINTS: set[str] = {
    "STATIC_LOCALE_DESTRUCTOR",
    "APPLICATION_SHUTDOWN",
    "MONTE_CARLO_LOOP",
    "APPLICATION_CONSTRUCTOR",
}

VALID_REMEDIATIONS: set[str] = {
    "PREINITIALIZE_LOCALE_FACETS_IN_USER_INIT",
    "CALL_QUICK_EXIT_IN_USER_CLEANUP",
    "DISABLE_OPTIMIZATION_FLAGS",
    "INCREASE_CUSTOM_HEAP_CAPACITY",
    "DELETE_STATIC_DESTRUCTORS",
}

VALID_EVIDENCE_IDS: set[str] = {
    "EVID_GDB_SIGSEGV_FREE",
    "EVID_VALGRIND_INVALID_FREE",
    "EVID_MAIN_OPERATOR_NEW_DELEGATION",
    "EVID_MAIN_SHUTDOWN_HEAP_FREE",
    "EVID_RELEASE_LOG_EXIT_139",
    "EVID_DEBUG_LOG_EXIT_0",
}

HYPOTHESIS_ITEM_KEYS: set[str] = {"hypothesis_id", "rank", "likelihood", "rationale"}
TECHNICAL_NOTES_KEYS: set[str] = {"root_cause_explanation", "recovery_explanation"}


class SubmissionError(ValueError):
    """A malformed diagnostic submission."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _validate_json_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > 10:
            raise SubmissionError("JSON exceeds maximum nesting depth (10)")
        if isinstance(current, dict):
            if len(current) > 64:
                raise SubmissionError("JSON object exceeds maximum property limit (64)")
            stack.extend((v, depth + 1) for v in current.values())
        elif isinstance(current, list):
            if len(current) > 128:
                raise SubmissionError("JSON list exceeds maximum length limit (128)")
            stack.extend((child, depth + 1) for child in current)


def _text(value: object, *, field: str, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise SubmissionError(f"{field} must be a non-empty string under {max_len} characters")
    return value.strip()


def _int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100:
        raise SubmissionError(f"{field} must be a positive integer between 1 and 100")
    return value


def _validate_submission(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SubmissionError("root submission must be a JSON object")
    if set(value) != TOP_LEVEL_KEYS:
        missing = sorted(TOP_LEVEL_KEYS - set(value))
        extra = sorted(set(value) - TOP_LEVEL_KEYS)
        errs = []
        if missing:
            errs.append(f"missing keys: {missing}")
        if extra:
            errs.append(f"unknown keys: {extra}")
        raise SubmissionError("; ".join(errs))

    schema_version = _text(value["schema_version"], field="schema_version", max_len=64)
    if schema_version != "rolebench.diagnosis/v2":
        raise SubmissionError(f"unsupported schema_version: {schema_version!r} (expected rolebench.diagnosis/v2)")

    fault_summary = _text(value["fault_summary"], field="fault_summary", max_len=2048)

    prim_hyp = _text(value["primary_hypothesis_id"], field="primary_hypothesis_id", max_len=128)
    if prim_hyp not in VALID_PRIMARY_HYPOTHESES:
        raise SubmissionError(f"invalid primary_hypothesis_id: {prim_hyp!r}")

    fault_cat = _text(value["fault_category"], field="fault_category", max_len=128)
    if fault_cat not in VALID_FAULT_CATEGORIES:
        raise SubmissionError(f"invalid fault_category: {fault_cat!r}")

    trigger = _text(value["trigger_id"], field="trigger_id", max_len=128)
    if trigger not in VALID_TRIGGERS:
        raise SubmissionError(f"invalid trigger_id: {trigger!r}")

    failure_pt = _text(value["failure_point_id"], field="failure_point_id", max_len=128)
    if failure_pt not in VALID_FAILURE_POINTS:
        raise SubmissionError(f"invalid failure_point_id: {failure_pt!r}")

    remediation = _text(value["remediation_action_id"], field="remediation_action_id", max_len=128)
    if remediation not in VALID_REMEDIATIONS:
        raise SubmissionError(f"invalid remediation_action_id: {remediation!r}")

    raw_evidence = value["cited_evidence_ids"]
    if not isinstance(raw_evidence, list) or len(raw_evidence) < 2 or len(raw_evidence) > 16:
        raise SubmissionError("cited_evidence_ids must be a list containing 2 to 16 evidence IDs")

    normalized_evidence: list[str] = []
    seen_ev = set()
    for idx, ev_item in enumerate(raw_evidence):
        ev_id = _text(ev_item, field=f"cited_evidence_ids[{idx}]", max_len=128)
        if ev_id not in VALID_EVIDENCE_IDS:
            raise SubmissionError(f"invalid evidence ID: {ev_id!r}")
        if ev_id in seen_ev:
            raise SubmissionError(f"duplicate evidence ID: {ev_id!r}")
        seen_ev.add(ev_id)
        normalized_evidence.append(ev_id)
    normalized_evidence.sort()

    raw_hyp = value["hypotheses_ranking"]
    if not isinstance(raw_hyp, list) or len(raw_hyp) < 2 or len(raw_hyp) > 16:
        raise SubmissionError("hypotheses_ranking must be a list containing 2 to 16 hypotheses")

    normalized_hyp: list[dict[str, object]] = []
    seen_h_ids = set()
    seen_ranks = set()
    for idx, item in enumerate(raw_hyp):
        if not isinstance(item, dict) or set(item) != HYPOTHESIS_ITEM_KEYS:
            raise SubmissionError(f"hypotheses_ranking[{idx}] has invalid keys")
        h_id = _text(item["hypothesis_id"], field=f"hypotheses_ranking[{idx}].hypothesis_id", max_len=128)
        if h_id not in VALID_PRIMARY_HYPOTHESES:
            raise SubmissionError(f"invalid hypothesis_id: {h_id!r}")
        if h_id in seen_h_ids:
            raise SubmissionError(f"duplicate hypothesis_id: {h_id!r}")
        seen_h_ids.add(h_id)

        h_rank = _int(item["rank"], field=f"hypotheses_ranking[{idx}].rank")
        if h_rank in seen_ranks:
            raise SubmissionError(f"duplicate hypothesis rank: {h_rank}")
        seen_ranks.add(h_rank)

        h_like = _text(item["likelihood"], field=f"hypotheses_ranking[{idx}].likelihood", max_len=32)
        if h_like.lower() not in {"high", "medium", "low", "falsified", "probable", "improbable"}:
            raise SubmissionError(f"invalid likelihood: {h_like!r}")

        h_rat = _text(item["rationale"], field=f"hypotheses_ranking[{idx}].rationale", max_len=4096)
        normalized_hyp.append({
            "hypothesis_id": h_id,
            "likelihood": h_like.lower(),
            "rank": h_rank,
            "rationale": h_rat,
        })

    sorted_ranks = sorted(h["rank"] for h in normalized_hyp)
    if sorted_ranks != list(range(1, len(normalized_hyp) + 1)):
        raise SubmissionError("hypothesis ranks must be sequential starting at 1")
    normalized_hyp.sort(key=lambda h: int(h["rank"]))

    raw_notes = value["technical_notes"]
    if not isinstance(raw_notes, dict) or set(raw_notes) != TECHNICAL_NOTES_KEYS:
        raise SubmissionError("technical_notes must be an object with root_cause_explanation and recovery_explanation")
    rc_exp = _text(raw_notes["root_cause_explanation"], field="technical_notes.root_cause_explanation", max_len=4096)
    rec_exp = _text(raw_notes["recovery_explanation"], field="technical_notes.recovery_explanation", max_len=4096)

    normalized_notes = {
        "recovery_explanation": rec_exp,
        "root_cause_explanation": rc_exp,
    }

    return {
        "cited_evidence_ids": normalized_evidence,
        "failure_point_id": failure_pt,
        "fault_category": fault_cat,
        "fault_summary": fault_summary,
        "hypotheses_ranking": normalized_hyp,
        "primary_hypothesis_id": prim_hyp,
        "remediation_action_id": remediation,
        "schema_version": "rolebench.diagnosis/v2",
        "technical_notes": normalized_notes,
        "trigger_id": trigger,
    }


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        snapshot = {
            "schema_version": "rolebench.custom-memory-runner-snapshot/v1",
            "status": "rejected",
            "error": "artifact exceeds maximum allowed size (64 KiB)",
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    if not payload:
        snapshot = {
            "schema_version": "rolebench.custom-memory-runner-snapshot/v1",
            "status": "rejected",
            "error": "empty submission payload",
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    try:
        text_payload = payload.decode("utf-8")
    except UnicodeDecodeError:
        snapshot = {
            "schema_version": "rolebench.custom-memory-runner-snapshot/v1",
            "status": "rejected",
            "error": "payload is not valid UTF-8",
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    try:
        parsed = json.loads(
            text_payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _validate_json_shape(parsed)
        submission = _validate_submission(parsed)
    except Exception as exc:
        snapshot = {
            "schema_version": "rolebench.custom-memory-runner-snapshot/v1",
            "status": "rejected",
            "error": str(exc),
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    snapshot = {
        "schema_version": "rolebench.custom-memory-runner-snapshot/v1",
        "status": "applied",
        "error": None,
        "submission": submission,
    }
    sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
