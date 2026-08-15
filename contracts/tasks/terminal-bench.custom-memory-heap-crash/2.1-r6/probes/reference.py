#!/usr/bin/env python3
"""Deterministic reference admission probe for custom-memory-heap-crash."""

from __future__ import annotations

import json
import sys

REFERENCE_DIAGNOSIS = {
    "cited_evidence_ids": [
        "EVID_GDB_SIGSEGV_FREE",
        "EVID_MAIN_OPERATOR_NEW_DELEGATION",
        "EVID_MAIN_SHUTDOWN_HEAP_FREE",
        "EVID_VALGRIND_INVALID_FREE"
    ],
    "failure_point_id": "STATIC_LOCALE_DESTRUCTOR",
    "fault_category": "ALLOCATOR_LIFETIME_MISMATCH",
    "fault_summary": "Release binary crashes during static destruction after main() returns due to custom heap allocator lifetime conflict with lazily registered locale facet structures.",
    "hypotheses_ranking": [
        {
            "hypothesis_id": "HYP_CUSTOM_HEAP_LOCALE_DESTRUCTION",
            "likelihood": "high",
            "rank": 1,
            "rationale": "Locale facets lazily allocated via global operator new from CustomHeapManager during simulation are deleted after CustomHeapManager is freed in shutdown."
        },
        {
            "hypothesis_id": "HYP_MONTE_CARLO_STACK_CORRUPTION",
            "likelihood": "low",
            "rank": 2,
            "rationale": "Falsified because computation runs to complete numerical output and accuracy reporting before exit crash."
        }
    ],
    "primary_hypothesis_id": "HYP_CUSTOM_HEAP_LOCALE_DESTRUCTION",
    "remediation_action_id": "PREINITIALIZE_LOCALE_FACETS_IN_USER_INIT",
    "schema_version": "rolebench.diagnosis/v2",
    "technical_notes": {
        "recovery_explanation": "Formatting via stringstream in user_init() triggers lazy facet registration while g_custom_heap is null, causing allocations to use system malloc. When freed in static cleanup, standard free handles the blocks safely.",
        "root_cause_explanation": "In release mode, global operator new routes facet allocations to CustomHeapManager. Application::~Application destroys the custom heap before static exit handlers execute _Fac_tidy_reg_t destructors."
    },
    "trigger_id": "STREAM_OUTPUT_FORMATTING"
}


def main() -> None:
    sys.stdout.write(json.dumps(REFERENCE_DIAGNOSIS, indent=2) + "\n")


if __name__ == "__main__":
    main()
