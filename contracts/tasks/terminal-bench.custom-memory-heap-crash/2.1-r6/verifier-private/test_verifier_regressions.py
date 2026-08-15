#!/usr/bin/env python3
"""Regression tests for diagnostic prose quality gates."""

from __future__ import annotations

import json
from pathlib import Path
import runpy

TASK_DIR = Path(__file__).resolve().parent.parent
VERIFIER = runpy.run_path(str(TASK_DIR / "verifier-private" / "verifier.py"))
IS_SUBSTANTIVE_TEXT = VERIFIER["_is_substantive_text"]
REFERENCE = runpy.run_path(str(TASK_DIR / "probes" / "reference.py"))["REFERENCE_DIAGNOSIS"]


def test_reference_explanations_are_substantive() -> None:
    assert IS_SUBSTANTIVE_TEXT(REFERENCE["fault_summary"], min_chars=80, min_words=10)
    for hypothesis in REFERENCE["hypotheses_ranking"]:
        assert IS_SUBSTANTIVE_TEXT(hypothesis["rationale"], min_chars=60, min_words=8)
    assert IS_SUBSTANTIVE_TEXT(
        REFERENCE["technical_notes"]["root_cause_explanation"], min_chars=120, min_words=15
    )
    assert IS_SUBSTANTIVE_TEXT(
        REFERENCE["technical_notes"]["recovery_explanation"], min_chars=120, min_words=15
    )


def test_one_character_summary_is_rejected() -> None:
    assert not IS_SUBSTANTIVE_TEXT("x", min_chars=80, min_words=10)


def test_template_rationale_is_rejected() -> None:
    assert not IS_SUBSTANTIVE_TEXT(
        "<supporting evidence and diagnostic reasoning>", min_chars=60, min_words=8
    )


def test_repeated_filler_is_rejected() -> None:
    assert not IS_SUBSTANTIVE_TEXT("filler " * 40, min_chars=120, min_words=15)


def test_todo_recovery_note_is_rejected() -> None:
    assert not IS_SUBSTANTIVE_TEXT(
        "TODO replace this text with a complete and carefully reasoned recovery explanation",
        min_chars=120,
        min_words=15,
    )


if __name__ == "__main__":
    test_reference_explanations_are_substantive()
    test_one_character_summary_is_rejected()
    test_template_rationale_is_rejected()
    test_repeated_filler_is_rejected()
    test_todo_recovery_note_is_rejected()
    print("memory verifier regressions passed")
