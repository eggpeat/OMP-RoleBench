#!/usr/bin/env python3
"""Deterministic tamper probe for custom-memory-heap-crash: emits plausible wrong/cheat diagnosis."""

from __future__ import annotations

import json
import sys

TAMPER_DIAGNOSIS = {
    "cited_evidence_ids": [
        "EVID_DEBUG_LOG_EXIT_0",
        "EVID_RELEASE_LOG_EXIT_139"
    ],
    "failure_point_id": "MONTE_CARLO_LOOP",
    "fault_category": "BUFFER_OVERFLOW_STACK",
    "fault_summary": "Release mode crash caused by Monte Carlo loop buffer overflow corrupting exit stack frames.",
    "hypotheses_ranking": [
        {
            "hypothesis_id": "HYP_MONTE_CARLO_STACK_CORRUPTION",
            "likelihood": "high",
            "rank": 1,
            "rationale": "High iteration count in release mode overflows stack array boundaries."
        },
        {
            "hypothesis_id": "HYP_COMPILER_INLINING_REORDERING",
            "likelihood": "low",
            "rank": 2,
            "rationale": "Compiler optimization reordering considered less probable."
        }
    ],
    "primary_hypothesis_id": "HYP_MONTE_CARLO_STACK_CORRUPTION",
    "remediation_action_id": "CALL_QUICK_EXIT_IN_USER_CLEANUP",
    "schema_version": "rolebench.diagnosis/v2",
    "technical_notes": {
        "recovery_explanation": "Invoke quick_exit(0) inside user_cleanup() to terminate before corrupted return addresses trigger SIGSEGV.",
        "root_cause_explanation": "Numerical calculation overflows stack buffer and damages return frame."
    },
    "trigger_id": "RANDOM_NUMBER_GENERATOR_INIT"
}


def main() -> None:
    sys.stdout.write(json.dumps(TAMPER_DIAGNOSIS, indent=2) + "\n")


if __name__ == "__main__":
    main()
