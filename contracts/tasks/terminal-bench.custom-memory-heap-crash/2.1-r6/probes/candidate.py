#!/usr/bin/env python3
"""Provider-disabled reference candidate for calibration-only task publication."""

from __future__ import annotations

import json
import sys

# Independent prose formulation sharing exact categorical invariants with reference
CANDIDATE_DIAGNOSIS = {
    "cited_evidence_ids": [
        "EVID_GDB_SIGSEGV_FREE",
        "EVID_RELEASE_LOG_EXIT_139",
        "EVID_VALGRIND_INVALID_FREE"
    ],
    "failure_point_id": "STATIC_LOCALE_DESTRUCTOR",
    "fault_category": "ALLOCATOR_LIFETIME_MISMATCH",
    "fault_summary": "Exit-phase segmentation fault in release mode triggered by static locale facet destructors deallocating memory against a destroyed CustomHeapManager instance.",
    "hypotheses_ranking": [
        {
            "hypothesis_id": "HYP_CUSTOM_HEAP_LOCALE_DESTRUCTION",
            "likelihood": "high",
            "rank": 1,
            "rationale": "Static destruction sequence invokes operator delete on facet nodes whose backing memory belonged to the bump heap torn down during Application shutdown."
        },
        {
            "hypothesis_id": "HYP_COMPILER_INLINING_REORDERING",
            "likelihood": "low",
            "rank": 2,
            "rationale": "Compiler optimization reordering is ruled out as the backtrace confirms standard atexit handler execution order."
        }
    ],
    "primary_hypothesis_id": "HYP_CUSTOM_HEAP_LOCALE_DESTRUCTION",
    "remediation_action_id": "PREINITIALIZE_LOCALE_FACETS_IN_USER_INIT",
    "schema_version": "rolebench.diagnosis/v2",
    "technical_notes": {
        "recovery_explanation": "Force facet allocation on the default system heap during user_init before constructing the custom allocator, so static cleanup later returns those blocks through the matching system deallocator.",
        "root_cause_explanation": "Lifetime inversion where static runtime facets outlive the custom heap allocator that serviced their dynamic allocation."
    },
    "trigger_id": "STREAM_OUTPUT_FORMATTING"
}


def main() -> None:
    sys.stdout.write(json.dumps(CANDIDATE_DIAGNOSIS, indent=2) + "\n")


if __name__ == "__main__":
    main()
