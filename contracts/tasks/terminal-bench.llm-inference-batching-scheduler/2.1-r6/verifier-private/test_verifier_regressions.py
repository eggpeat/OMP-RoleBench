#!/usr/bin/env python3
"""Regression tests for scheduler plan semantic verification."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import runpy

TASK_DIR = Path(__file__).resolve().parent.parent
VERIFIER = runpy.run_path(str(TASK_DIR / "verifier-private" / "verifier.py"))
VERIFY_PLAN = VERIFIER["_verify_plan"]
EXPECTED = json.loads((TASK_DIR / "verifier-private" / "expected_plan_graph.json").read_text(encoding="utf-8"))
REFERENCE = runpy.run_path(str(TASK_DIR / "probes" / "reference.py"))["REFERENCE_PLAN"]
TAMPER = runpy.run_path(str(TASK_DIR / "probes" / "tamper.py"))["TAMPER_PLAN"]


def test_reference_plan_is_accepted() -> None:
    assert VERIFY_PLAN(REFERENCE, EXPECTED)


def test_commit_must_consume_staged_plans_and_gate_verdict() -> None:
    assert not VERIFY_PLAN(TAMPER, EXPECTED)


def test_duplicate_component_identity_is_rejected() -> None:
    mutant = copy.deepcopy(REFERENCE)
    mutant["architecture"]["components"].append(copy.deepcopy(mutant["architecture"]["components"][0]))
    assert not VERIFY_PLAN(mutant, EXPECTED)


def test_duplicate_invariant_identity_is_rejected() -> None:
    mutant = copy.deepcopy(REFERENCE)
    shadow = copy.deepcopy(mutant["invariants"][0])
    mutant["invariants"].append(shadow)
    assert not VERIFY_PLAN(mutant, EXPECTED)

def test_service_level_model_uses_exact_shape_limit_and_cost_equation() -> None:
    mutant = copy.deepcopy(REFERENCE)
    mutant["service_level_model"]["compiled_shape_limit"] = 9
    assert not VERIFY_PLAN(mutant, EXPECTED)


def test_unknown_step_input_is_rejected_without_verifier_error() -> None:
    mutant = copy.deepcopy(REFERENCE)
    mutant["execution_graph"]["steps"][1]["inputs"].append("IFACE_UNKNOWN")
    assert not VERIFY_PLAN(mutant, EXPECTED)


def test_interface_kinds_are_bound_to_producer_operations() -> None:
    mutant = copy.deepcopy(REFERENCE)
    interfaces = mutant["architecture"]["interfaces"]
    interfaces[0]["interface_kind"], interfaces[1]["interface_kind"] = (
        interfaces[1]["interface_kind"],
        interfaces[0]["interface_kind"],
    )
    assert not VERIFY_PLAN(mutant, EXPECTED)


def test_risk_mitigation_steps_must_exist() -> None:
    mutant = copy.deepcopy(REFERENCE)
    mutant["risks_and_mitigations"]["risks"][0]["mitigation_step_ids"] = ["STEP_DOES_NOT_EXIST"]
    assert not VERIFY_PLAN(mutant, EXPECTED)
def test_cyclic_step_dependencies_are_rejected() -> None:
    mutant = copy.deepcopy(REFERENCE)
    steps = mutant["execution_graph"]["steps"]
    # Create cycle: step 0 depends on step 1
    steps[0]["depends_on"].append(steps[1]["step_id"])
    assert not VERIFY_PLAN(mutant, EXPECTED)


def test_commit_before_gate_evaluation_is_rejected() -> None:
    mutant = copy.deepcopy(REFERENCE)
    steps = mutant["execution_graph"]["steps"]
    for s in steps:
        if s.get("operation_kind") == "COMMIT_OUTPUTS":
            s["depends_on"] = []
    assert not VERIFY_PLAN(mutant, EXPECTED)



if __name__ == "__main__":
    test_reference_plan_is_accepted()
    test_commit_must_consume_staged_plans_and_gate_verdict()
    test_duplicate_component_identity_is_rejected()
    test_duplicate_invariant_identity_is_rejected()
    test_service_level_model_uses_exact_shape_limit_and_cost_equation()
    test_unknown_step_input_is_rejected_without_verifier_error()
    test_interface_kinds_are_bound_to_producer_operations()
    test_risk_mitigation_steps_must_exist()
    test_cyclic_step_dependencies_are_rejected()
    test_commit_before_gate_evaluation_is_rejected()
    print("scheduler verifier regressions passed")
