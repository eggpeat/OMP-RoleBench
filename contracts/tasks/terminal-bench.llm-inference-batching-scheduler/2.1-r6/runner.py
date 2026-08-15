#!/usr/bin/env python3
"""Deterministic data-only runner for LLM inference batching scheduler plan submissions."""

from __future__ import annotations

import json
import math
import re
import sys
from typing import Any, NoReturn

MAX_ARTIFACT_BYTES: int = 128 * 1024  # 131072 bytes (128 KiB)

SCHEMA_VERSION: str = "rolebench.scheduler-plan/v1"
RUNNER_SNAPSHOT_SCHEMA_VERSION: str = "rolebench.scheduler-plan-runner-snapshot/v1"
TASK_DOMAIN: str = "llm-inference-batching-scheduler"

ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]+$")

VALID_STATE_SCOPES: set[str] = {
    "stateless",
    "read-only",
    "ephemeral-staging",
    "persistent-target",
}

VALID_IMMUTABILITY_MODES: set[str] = {
    "immutable",
    "single-write-sealed",
    "mutable-staging",
}

VALID_INTERFACE_KINDS: set[str] = {
    "RAW_REQUEST_FEEDS",
    "GLOBAL_SHAPE_CATALOG",
    "STAGED_BATCH_PLANS_B1",
    "STAGED_BATCH_PLANS_B2",
    "GATE_VERDICT_REPORT",
    "COMMITTED_PLAN_MANIFESTS",
}

VALID_PHASES: set[str] = {
    "INGEST_AND_VALIDATE",
    "GLOBAL_SHAPE_ALLOCATION",
    "BUCKET_BATCH_PACKING",
    "COST_AND_SLA_VALIDATION",
    "ATOMIC_COMMIT_AND_ROLLBACK",
}

VALID_OPERATION_KINDS: set[str] = {
    "READ_INPUTS",
    "SYNTHESIZE_GLOBAL_SHAPES",
    "ASSIGN_BATCHES",
    "EVALUATE_GATES",
    "COMMIT_OUTPUTS",
    "ROLLBACK_ON_FAILURE",
}

PHASE_OPERATION_MAP: dict[str, str] = {
    "READ_INPUTS": "INGEST_AND_VALIDATE",
    "SYNTHESIZE_GLOBAL_SHAPES": "GLOBAL_SHAPE_ALLOCATION",
    "ASSIGN_BATCHES": "BUCKET_BATCH_PACKING",
    "EVALUATE_GATES": "COST_AND_SLA_VALIDATION",
    "COMMIT_OUTPUTS": "ATOMIC_COMMIT_AND_ROLLBACK",
    "ROLLBACK_ON_FAILURE": "ATOMIC_COMMIT_AND_ROLLBACK",
}

VALID_INVARIANT_CATEGORIES: set[str] = {
    "EXACT_ONCE_ASSIGNMENT",
    "TENSOR_SHAPE_ALIGNMENT",
    "GLOBAL_SHAPE_BUDGET_LE_8",
    "COST_AND_LATENCY_GATES",
    "IMMUTABLE_INPUTS",
    "ATOMIC_TRANSACTION_OR_ROLLBACK",
    "DETERMINISTIC_TIE_BREAKING",
}

VALID_INVARIANT_SCOPES: set[str] = {
    "GLOBAL",
    "CROSS_BUCKET",
    "PER_BUCKET",
    "STORAGE",
}

VALID_GATE_CATEGORIES: set[str] = {
    "GLOBAL_SHAPE_BUDGET",
    "REQUEST_INTEGRITY",
    "SLA_AND_COST_BOUNDS",
    "ATOMIC_WRITE_VERIFICATION",
}

VALID_RISK_SEVERITIES: set[str] = {
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
}

VALID_GATE_FALLBACKS: set[str] = {
    "ABORT_AND_ROLLBACK",
    "ALERT_AND_RETRY",
    "FAIL_CLOSED",
}

VALID_COMMIT_STRATEGIES: set[str] = {
    "MANIFEST_POINTER_SWAP",
    "GENERATION_DIRECTORY_SWAP",
    "WRITE_AHEAD_LOG_RENAME",
    "TWO_PHASE_TRANSACTION",
}

VALID_VERIFICATION_METHODS: set[str] = {
    "READ_ONLY_ACCESS",
    "CHECKSUM_VERIFICATION",
    "NO_INPLACE_MUTATION",
}

VALID_CAPABILITIES: set[str] = {
    "system-architecture",
    "task-decomposition",
    "dependency-sequencing",
    "interface-design",
    "risk-identification",
}


class ValidationError(ValueError):
    """Structured plan validation failure."""


def _reject_constant(value: str) -> NoReturn:
    raise ValidationError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _validate_identifier(val: Any, field_name: str, min_len: int = 1, max_len: int = 128) -> str:
    if not isinstance(val, str) or isinstance(val, bool):
        raise ValidationError(f"{field_name} must be a string, got {type(val).__name__}")
    if len(val) < min_len or len(val) > max_len:
        raise ValidationError(f"{field_name} length {len(val)} out of bounds [{min_len}, {max_len}]")
    if not ID_PATTERN.match(val):
        raise ValidationError(f"{field_name} {val!r} does not match required identifier pattern ^[A-Za-z0-9_.-]+$")
    return val


def _validate_string(val: Any, field_name: str, min_len: int = 1, max_len: int = 2048) -> str:
    if not isinstance(val, str) or isinstance(val, bool):
        raise ValidationError(f"{field_name} must be a string, got {type(val).__name__}")
    if len(val) < min_len or len(val) > max_len:
        raise ValidationError(f"{field_name} length {len(val)} out of bounds [{min_len}, {max_len}]")
    if not val.strip():
        raise ValidationError(f"{field_name} must not be blank/whitespace-only")
    return val


def _validate_identifier_list(val: Any, field_name: str, min_items: int = 0, max_items: int = 64) -> list[str]:
    if not isinstance(val, list):
        raise ValidationError(f"{field_name} must be a list, got {type(val).__name__}")
    if len(val) < min_items or len(val) > max_items:
        raise ValidationError(f"{field_name} count {len(val)} out of bounds [{min_items}, {max_items}]")
    result: list[str] = []
    for idx, item in enumerate(val):
        result.append(_validate_identifier(item, f"{field_name}[{idx}]"))
    return result


def _validate_string_list(val: Any, field_name: str, min_items: int = 0, max_items: int = 64) -> list[str]:
    if not isinstance(val, list):
        raise ValidationError(f"{field_name} must be a list, got {type(val).__name__}")
    if len(val) < min_items or len(val) > max_items:
        raise ValidationError(f"{field_name} count {len(val)} out of bounds [{min_items}, {max_items}]")
    result: list[str] = []
    for idx, item in enumerate(val):
        result.append(_validate_string(item, f"{field_name}[{idx}]"))
    return result


def _validate_submission(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError("root artifact must be a JSON object")

    expected_top_keys = {
        "schema_version",
        "plan_id",
        "task_domain",
        "architecture",
        "execution_graph",
        "service_level_model",
        "invariants",
        "risks_and_mitigations",
        "rollout_and_recovery",
    }
    if set(data) != expected_top_keys:
        missing = expected_top_keys - set(data)
        extra = set(data) - expected_top_keys
        raise ValidationError(f"root artifact key mismatch: missing={missing}, extra={extra}")

    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError(f"invalid schema_version: {data.get('schema_version')!r} != {SCHEMA_VERSION!r}")
    if data.get("task_domain") != TASK_DOMAIN:
        raise ValidationError(f"invalid task_domain: {data.get('task_domain')!r} != {TASK_DOMAIN!r}")

    plan_id = _validate_identifier(data.get("plan_id"), "plan_id")

    # 1. Architecture
    arch = data.get("architecture")
    if not isinstance(arch, dict) or set(arch) != {"components", "interfaces"}:
        raise ValidationError("architecture must contain exactly 'components' and 'interfaces'")

    raw_components = arch.get("components")
    if not isinstance(raw_components, list) or len(raw_components) < 2 or len(raw_components) > 32:
        raise ValidationError("architecture.components must be a list with 2 to 32 components")

    components: list[dict[str, Any]] = []
    component_ids: set[str] = set()
    for idx, c in enumerate(raw_components):
        if not isinstance(c, dict):
            raise ValidationError(f"components[{idx}] must be a dict")
        expected_comp_keys = {
            "component_id",
            "name",
            "role",
            "responsibilities",
            "state_scope",
            "failure_domain",
        }
        if set(c) != expected_comp_keys:
            raise ValidationError(f"components[{idx}] key mismatch: {set(c)} != {expected_comp_keys}")
        cid = _validate_identifier(c["component_id"], f"components[{idx}].component_id")
        if cid in component_ids:
            raise ValidationError(f"duplicate component_id: {cid!r}")
        component_ids.add(cid)
        c_name = _validate_string(c["name"], f"components[{idx}].name", 1, 128)
        c_role = _validate_string(c["role"], f"components[{idx}].role", 1, 128)
        c_resp = _validate_string_list(c["responsibilities"], f"components[{idx}].responsibilities", min_items=1, max_items=32)
        c_scope = _validate_string(c["state_scope"], f"components[{idx}].state_scope", 1, 64)
        if c_scope not in VALID_STATE_SCOPES:
            raise ValidationError(f"invalid state_scope {c_scope!r} in components[{idx}]")
        c_fail = _validate_string(c["failure_domain"], f"components[{idx}].failure_domain", 1, 128)
        components.append({
            "component_id": cid,
            "name": c_name,
            "role": c_role,
            "responsibilities": c_resp,
            "state_scope": c_scope,
            "failure_domain": c_fail,
        })

    raw_interfaces = arch.get("interfaces")
    if not isinstance(raw_interfaces, list) or len(raw_interfaces) < 2 or len(raw_interfaces) > 32:
        raise ValidationError("architecture.interfaces must be a list with 2 to 32 interfaces")

    interfaces: list[dict[str, Any]] = []
    interface_ids: set[str] = set()
    for idx, iface in enumerate(raw_interfaces):
        if not isinstance(iface, dict):
            raise ValidationError(f"interfaces[{idx}] must be a dict")
        expected_iface_keys = {
            "interface_id",
            "interface_kind",
            "name",
            "producer_component_id",
            "consumer_component_ids",
            "data_contract",
            "immutability",
        }
        if set(iface) != expected_iface_keys:
            raise ValidationError(f"interfaces[{idx}] key mismatch: {set(iface)} != {expected_iface_keys}")
        if_id = _validate_identifier(iface["interface_id"], f"interfaces[{idx}].interface_id")
        if if_id in interface_ids:
            raise ValidationError(f"duplicate interface_id: {if_id!r}")
        interface_ids.add(if_id)
        if_kind = _validate_string(iface["interface_kind"], f"interfaces[{idx}].interface_kind", 1, 64)
        if if_kind not in VALID_INTERFACE_KINDS:
            raise ValidationError(f"invalid interface_kind {if_kind!r} in interfaces[{idx}]")
        if_name = _validate_string(iface["name"], f"interfaces[{idx}].name", 1, 128)
        prod = _validate_identifier(iface["producer_component_id"], f"interfaces[{idx}].producer_component_id")
        if prod not in component_ids:
            raise ValidationError(f"interfaces[{idx}].producer_component_id {prod!r} does not exist in components")
        consumers = _validate_identifier_list(iface["consumer_component_ids"], f"interfaces[{idx}].consumer_component_ids", min_items=1, max_items=32)
        for c_idx, c_id in enumerate(consumers):
            if c_id not in component_ids:
                raise ValidationError(f"interfaces[{idx}].consumer_component_ids[{c_idx}] {c_id!r} not in components")
        data_contract = _validate_string(iface["data_contract"], f"interfaces[{idx}].data_contract", 1, 2048)
        immut = _validate_string(iface["immutability"], f"interfaces[{idx}].immutability", 1, 64)
        if immut not in VALID_IMMUTABILITY_MODES:
            raise ValidationError(f"invalid immutability {immut!r} in interfaces[{idx}]")
        interfaces.append({
            "interface_id": if_id,
            "interface_kind": if_kind,
            "name": if_name,
            "producer_component_id": prod,
            "consumer_component_ids": consumers,
            "data_contract": data_contract,
            "immutability": immut,
        })

    # 2. Invariants
    raw_invariants = data.get("invariants")
    if not isinstance(raw_invariants, list) or len(raw_invariants) < 7 or len(raw_invariants) > 32:
        raise ValidationError("invariants must be a list with 7 to 32 invariants")

    invariants: list[dict[str, Any]] = []
    invariant_ids: set[str] = set()
    invariant_categories: set[str] = set()
    for idx, inv in enumerate(raw_invariants):
        if not isinstance(inv, dict):
            raise ValidationError(f"invariants[{idx}] must be a dict")
        expected_inv_keys = {
            "invariant_id",
            "invariant_category",
            "name",
            "description",
            "scope",
            "enforcing_step_ids",
            "verification_gate_ids",
        }
        if set(inv) != expected_inv_keys:
            raise ValidationError(f"invariants[{idx}] key mismatch: {set(inv)} != {expected_inv_keys}")
        inv_id = _validate_identifier(inv["invariant_id"], f"invariants[{idx}].invariant_id")
        if inv_id in invariant_ids:
            raise ValidationError(f"duplicate invariant_id: {inv_id!r}")
        invariant_ids.add(inv_id)
        inv_cat = _validate_string(inv["invariant_category"], f"invariants[{idx}].invariant_category", 1, 64)
        if inv_cat not in VALID_INVARIANT_CATEGORIES:
            raise ValidationError(f"invalid invariant_category {inv_cat!r} in invariants[{idx}]")
        if inv_cat in invariant_categories:
            raise ValidationError(f"duplicate invariant_category {inv_cat!r} in invariants[{idx}]")
        invariant_categories.add(inv_cat)
        inv_name = _validate_string(inv["name"], f"invariants[{idx}].name", 1, 128)
        inv_desc = _validate_string(inv["description"], f"invariants[{idx}].description", 1, 2048)
        inv_scope = _validate_string(inv["scope"], f"invariants[{idx}].scope", 1, 64)
        if inv_scope not in VALID_INVARIANT_SCOPES:
            raise ValidationError(f"invalid invariant scope {inv_scope!r} in invariants[{idx}]")
        enf_steps = _validate_identifier_list(inv["enforcing_step_ids"], f"invariants[{idx}].enforcing_step_ids", min_items=1, max_items=32)
        ver_gates = _validate_identifier_list(inv["verification_gate_ids"], f"invariants[{idx}].verification_gate_ids", min_items=1, max_items=32)
        invariants.append({
            "invariant_id": inv_id,
            "invariant_category": inv_cat,
            "name": inv_name,
            "description": inv_desc,
            "scope": inv_scope,
            "enforcing_step_ids": enf_steps,
            "verification_gate_ids": ver_gates,
        })

    # 3. Risks & Gates
    raw_rg = data.get("risks_and_mitigations")
    if not isinstance(raw_rg, dict) or set(raw_rg) != {"risks", "gates"}:
        raise ValidationError("risks_and_mitigations must contain exactly 'risks' and 'gates'")

    raw_risks = raw_rg.get("risks")
    if not isinstance(raw_risks, list) or len(raw_risks) < 2 or len(raw_risks) > 32:
        raise ValidationError("risks_and_mitigations.risks must have 2 to 32 items")
    risks: list[dict[str, Any]] = []
    risk_ids: set[str] = set()
    for idx, r in enumerate(raw_risks):
        if not isinstance(r, dict):
            raise ValidationError(f"risks[{idx}] must be a dict")
        expected_r_keys = {"risk_id", "description", "severity", "mitigation_step_ids"}
        if set(r) != expected_r_keys:
            raise ValidationError(f"risks[{idx}] key mismatch: {set(r)} != {expected_r_keys}")
        r_id = _validate_identifier(r["risk_id"], f"risks[{idx}].risk_id")
        if r_id in risk_ids:
            raise ValidationError(f"duplicate risk_id: {r_id!r}")
        risk_ids.add(r_id)
        r_desc = _validate_string(r["description"], f"risks[{idx}].description", 1, 2048)
        r_sev = _validate_string(r["severity"], f"risks[{idx}].severity", 1, 64)
        if r_sev not in VALID_RISK_SEVERITIES:
            raise ValidationError(f"invalid risk severity {r_sev!r} in risks[{idx}]")
        r_mit = _validate_identifier_list(r["mitigation_step_ids"], f"risks[{idx}].mitigation_step_ids", min_items=1, max_items=32)
        risks.append({
            "risk_id": r_id,
            "description": r_desc,
            "severity": r_sev,
            "mitigation_step_ids": r_mit,
        })

    raw_gates = raw_rg.get("gates")
    if not isinstance(raw_gates, list) or len(raw_gates) < 4 or len(raw_gates) > 32:
        raise ValidationError("risks_and_mitigations.gates must have 4 to 32 items")
    gates: list[dict[str, Any]] = []
    gate_ids: set[str] = set()
    for idx, g in enumerate(raw_gates):
        if not isinstance(g, dict):
            raise ValidationError(f"gates[{idx}] must be a dict")
        expected_g_keys = {
            "gate_id",
            "gate_category",
            "name",
            "evaluation_step_id",
            "target_metric_or_invariant",
            "precondition_step_ids",
            "blocking",
            "fallback_action",
        }
        if set(g) != expected_g_keys:
            raise ValidationError(f"gates[{idx}] key mismatch: {set(g)} != {expected_g_keys}")
        g_id = _validate_identifier(g["gate_id"], f"gates[{idx}].gate_id")
        if g_id in gate_ids:
            raise ValidationError(f"duplicate gate_id: {g_id!r}")
        gate_ids.add(g_id)
        g_cat = _validate_string(g["gate_category"], f"gates[{idx}].gate_category", 1, 64)
        if g_cat not in VALID_GATE_CATEGORIES:
            raise ValidationError(f"invalid gate_category {g_cat!r} in gates[{idx}]")
        g_name = _validate_string(g["name"], f"gates[{idx}].name", 1, 128)
        g_eval = _validate_identifier(g["evaluation_step_id"], f"gates[{idx}].evaluation_step_id")
        g_target = _validate_identifier(g["target_metric_or_invariant"], f"gates[{idx}].target_metric_or_invariant")
        g_pre = _validate_identifier_list(g["precondition_step_ids"], f"gates[{idx}].precondition_step_ids", min_items=1, max_items=32)
        g_block = g.get("blocking")
        if not isinstance(g_block, bool):
            raise ValidationError(f"gates[{idx}].blocking must be boolean")
        g_fallback = _validate_string(g["fallback_action"], f"gates[{idx}].fallback_action", 1, 64)
        if g_fallback not in VALID_GATE_FALLBACKS:
            raise ValidationError(f"invalid gate fallback_action {g_fallback!r} in gates[{idx}]")
        gates.append({
            "gate_id": g_id,
            "gate_category": g_cat,
            "name": g_name,
            "evaluation_step_id": g_eval,
            "target_metric_or_invariant": g_target,
            "precondition_step_ids": g_pre,
            "blocking": g_block,
            "fallback_action": g_fallback,
        })

    # Validate invariant gate references
    for idx, inv in enumerate(invariants):
        for g_ref in inv["verification_gate_ids"]:
            if g_ref not in gate_ids:
                raise ValidationError(f"invariants[{idx}].verification_gate_ids {g_ref!r} not found in gates")

    # 4. Execution Graph Steps
    raw_graph = data.get("execution_graph")
    if not isinstance(raw_graph, dict) or set(raw_graph) != {"steps"}:
        raise ValidationError("execution_graph must contain exactly 'steps'")

    raw_steps = raw_graph.get("steps")
    if not isinstance(raw_steps, list) or len(raw_steps) < 4 or len(raw_steps) > 64:
        raise ValidationError("execution_graph.steps must be a list with 4 to 64 steps")

    steps: list[dict[str, Any]] = []
    step_ids: set[str] = set()
    for idx, s in enumerate(raw_steps):
        if not isinstance(s, dict):
            raise ValidationError(f"steps[{idx}] must be a dict")
        expected_step_keys = {
            "step_id",
            "name",
            "component_id",
            "phase",
            "operation_kind",
            "depends_on",
            "inputs",
            "outputs",
            "enforced_invariants",
            "required_capabilities",
        }
        if set(s) != expected_step_keys:
            raise ValidationError(f"steps[{idx}] key mismatch: {set(s)} != {expected_step_keys}")
        s_id = _validate_identifier(s["step_id"], f"steps[{idx}].step_id")
        if s_id in step_ids:
            raise ValidationError(f"duplicate step_id: {s_id!r}")
        step_ids.add(s_id)
        s_name = _validate_string(s["name"], f"steps[{idx}].name", 1, 128)
        s_comp = _validate_identifier(s["component_id"], f"steps[{idx}].component_id")
        if s_comp not in component_ids:
            raise ValidationError(f"steps[{idx}].component_id {s_comp!r} not found in components")
        s_phase = _validate_string(s["phase"], f"steps[{idx}].phase", 1, 64)
        if s_phase not in VALID_PHASES:
            raise ValidationError(f"invalid phase {s_phase!r} in steps[{idx}]")
        s_op = _validate_string(s["operation_kind"], f"steps[{idx}].operation_kind", 1, 64)
        if s_op not in VALID_OPERATION_KINDS:
            raise ValidationError(f"invalid operation_kind {s_op!r} in steps[{idx}]")
        expected_phase = PHASE_OPERATION_MAP.get(s_op)
        if s_phase != expected_phase:
            raise ValidationError(f"steps[{idx}] operation_kind {s_op!r} must be bound to phase {expected_phase!r}, got {s_phase!r}")
        s_dep = _validate_identifier_list(s["depends_on"], f"steps[{idx}].depends_on", min_items=0, max_items=32)
        s_in = _validate_identifier_list(s["inputs"], f"steps[{idx}].inputs", min_items=0, max_items=32)
        for in_idx, in_if in enumerate(s_in):
            if in_if not in interface_ids:
                raise ValidationError(f"steps[{idx}].inputs[{in_idx}] {in_if!r} not found in interfaces")
        s_out = _validate_identifier_list(s["outputs"], f"steps[{idx}].outputs", min_items=0, max_items=32)
        for out_idx, out_if in enumerate(s_out):
            if out_if not in interface_ids:
                raise ValidationError(f"steps[{idx}].outputs[{out_idx}] {out_if!r} not found in interfaces")
        s_inv = _validate_identifier_list(s["enforced_invariants"], f"steps[{idx}].enforced_invariants", min_items=0, max_items=32)
        for inv_idx, inv_ref in enumerate(s_inv):
            if inv_ref not in invariant_ids:
                raise ValidationError(f"steps[{idx}].enforced_invariants[{inv_idx}] {inv_ref!r} not found in invariants")
        s_caps = _validate_string_list(s["required_capabilities"], f"steps[{idx}].required_capabilities", min_items=1, max_items=16)
        for cap_idx, cap in enumerate(s_caps):
            if cap not in VALID_CAPABILITIES:
                raise ValidationError(f"invalid capability {cap!r} in steps[{idx}]")

        steps.append({
            "step_id": s_id,
            "name": s_name,
            "component_id": s_comp,
            "phase": s_phase,
            "operation_kind": s_op,
            "depends_on": s_dep,
            "inputs": s_in,
            "outputs": s_out,
            "enforced_invariants": s_inv,
            "required_capabilities": s_caps,
        })

    # Validate step dependencies point to declared steps
    for idx, s in enumerate(steps):
        for d_id in s["depends_on"]:
            if d_id not in step_ids:
                raise ValidationError(f"steps[{idx}].depends_on {d_id!r} not found in declared steps")

    # Validate cross-references to steps
    for idx, inv in enumerate(invariants):
        for s_ref in inv["enforcing_step_ids"]:
            if s_ref not in step_ids:
                raise ValidationError(f"invariants[{idx}].enforcing_step_ids {s_ref!r} not found in steps")

    for idx, r in enumerate(risks):
        for s_ref in r["mitigation_step_ids"]:
            if s_ref not in step_ids:
                raise ValidationError(f"risks[{idx}].mitigation_step_ids {s_ref!r} not found in steps")

    for idx, g in enumerate(gates):
        if g["evaluation_step_id"] not in step_ids:
            raise ValidationError(f"gates[{idx}].evaluation_step_id {g['evaluation_step_id']!r} not found in steps")
        for s_ref in g["precondition_step_ids"]:
            if s_ref not in step_ids:
                raise ValidationError(f"gates[{idx}].precondition_step_ids {s_ref!r} not found in steps")

    # 5. Structured service-level and cost model
    raw_service_model = data.get("service_level_model")
    service_model_keys = {
        "compiled_shape_metric_id",
        "compiled_shape_operator",
        "compiled_shape_limit",
        "latency_metric_id",
        "latency_aggregation",
        "latency_operator",
        "latency_limit_parameter_id",
        "padding_metric_id",
        "padding_operator",
        "padding_limit_parameter_id",
        "cost_metric_id",
        "cost_equation_id",
        "gpu_time_metric_id",
        "gpu_time_coefficient_parameter_id",
        "queue_overhead_metric_id",
        "queue_overhead_coefficient",
        "cost_operator",
        "cost_limit_parameter_id",
    }
    if not isinstance(raw_service_model, dict) or set(raw_service_model) != service_model_keys:
        raise ValidationError("service_level_model key mismatch")

    service_model: dict[str, Any] = {}
    for field_name in service_model_keys - {"compiled_shape_limit", "queue_overhead_coefficient"}:
        service_model[field_name] = _validate_identifier(
            raw_service_model[field_name], f"service_level_model.{field_name}"
        )
    for field_name in ("compiled_shape_limit", "queue_overhead_coefficient"):
        value = raw_service_model[field_name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 1024:
            raise ValidationError(f"service_level_model.{field_name} must be an integer from 1 to 1024")
        service_model[field_name] = value

    # 6. Rollout and Recovery
    raw_rr = data.get("rollout_and_recovery")
    if not isinstance(raw_rr, dict) or set(raw_rr) != {"atomic_commit_strategy", "input_preservation", "rollback_guarantees"}:
        raise ValidationError("rollout_and_recovery key mismatch")

    raw_commit = raw_rr.get("atomic_commit_strategy")
    expected_commit_keys = {"strategy_kind", "staging_directory", "atomic_publication_unit", "target_paths", "cleanup_on_abort"}
    if not isinstance(raw_commit, dict) or set(raw_commit) != expected_commit_keys:
        raise ValidationError(f"rollout_and_recovery.atomic_commit_strategy key mismatch: {set(raw_commit) if isinstance(raw_commit, dict) else None} != {expected_commit_keys}")
    strat_kind = _validate_string(raw_commit["strategy_kind"], "atomic_commit_strategy.strategy_kind", 1, 64)
    if strat_kind not in VALID_COMMIT_STRATEGIES:
        raise ValidationError(f"invalid strategy_kind {strat_kind!r}")
    staging_dir = _validate_string(raw_commit["staging_directory"], "atomic_commit_strategy.staging_directory", 1, 256)
    pub_unit = _validate_string(raw_commit["atomic_publication_unit"], "atomic_commit_strategy.atomic_publication_unit", 1, 256)
    target_paths = _validate_string_list(raw_commit["target_paths"], "atomic_commit_strategy.target_paths", min_items=2, max_items=16)
    cleanup = raw_commit.get("cleanup_on_abort")
    if not isinstance(cleanup, bool):
        raise ValidationError("atomic_commit_strategy.cleanup_on_abort must be boolean")

    raw_pres = raw_rr.get("input_preservation")
    if not isinstance(raw_pres, dict) or set(raw_pres) != {"immutable_paths", "verification_method"}:
        raise ValidationError("rollout_and_recovery.input_preservation key mismatch")
    immut_paths = _validate_string_list(raw_pres["immutable_paths"], "input_preservation.immutable_paths", min_items=2, max_items=16)
    ver_method = _validate_string(raw_pres["verification_method"], "input_preservation.verification_method", 1, 64)
    if ver_method not in VALID_VERIFICATION_METHODS:
        raise ValidationError(f"invalid verification_method {ver_method!r}")

    raw_roll = raw_rr.get("rollback_guarantees")
    if not isinstance(raw_roll, dict) or set(raw_roll) != {"preserves_inputs", "preserves_prior_outputs", "prevents_partial_writes", "rollback_step_ids"}:
        raise ValidationError("rollout_and_recovery.rollback_guarantees key mismatch")
    p_in = raw_roll.get("preserves_inputs")
    p_out = raw_roll.get("preserves_prior_outputs")
    p_part = raw_roll.get("prevents_partial_writes")
    if not isinstance(p_in, bool) or not isinstance(p_out, bool) or not isinstance(p_part, bool):
        raise ValidationError("rollback_guarantees boolean flags must be boolean")
    roll_steps = _validate_identifier_list(raw_roll["rollback_step_ids"], "rollback_guarantees.rollback_step_ids", min_items=1, max_items=16)
    for r_idx, r_step in enumerate(roll_steps):
        if r_step not in step_ids:
            raise ValidationError(f"rollback_guarantees.rollback_step_ids[{r_idx}] {r_step!r} not found in steps")

    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "task_domain": TASK_DOMAIN,
        "architecture": {
            "components": components,
            "interfaces": interfaces,
        },
        "execution_graph": {
            "steps": steps,
        },
        "service_level_model": service_model,
        "invariants": invariants,
        "risks_and_mitigations": {
            "risks": risks,
            "gates": gates,
        },
        "rollout_and_recovery": {
            "atomic_commit_strategy": {
                "strategy_kind": strat_kind,
                "staging_directory": staging_dir,
                "atomic_publication_unit": pub_unit,
                "target_paths": target_paths,
                "cleanup_on_abort": cleanup,
            },
            "input_preservation": {
                "immutable_paths": immut_paths,
                "verification_method": ver_method,
            },
            "rollback_guarantees": {
                "preserves_inputs": p_in,
                "preserves_prior_outputs": p_out,
                "prevents_partial_writes": p_part,
                "rollback_step_ids": roll_steps,
            },
        },
    }


def main() -> int:
    raw_bytes = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw_bytes) > MAX_ARTIFACT_BYTES:
        snapshot = {
            "schema_version": RUNNER_SNAPSHOT_SCHEMA_VERSION,
            "status": "rejected",
            "error": f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes limit",
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    if not raw_bytes.strip():
        snapshot = {
            "schema_version": RUNNER_SNAPSHOT_SCHEMA_VERSION,
            "status": "rejected",
            "error": "empty submission",
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        snapshot = {
            "schema_version": RUNNER_SNAPSHOT_SCHEMA_VERSION,
            "status": "rejected",
            "error": f"invalid UTF-8 encoding: {exc}",
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    try:
        parsed = json.loads(
            raw_text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        submission = _validate_submission(parsed)
    except Exception as exc:
        snapshot = {
            "schema_version": RUNNER_SNAPSHOT_SCHEMA_VERSION,
            "status": "rejected",
            "error": str(exc),
            "submission": None,
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    snapshot = {
        "schema_version": RUNNER_SNAPSHOT_SCHEMA_VERSION,
        "status": "applied",
        "error": None,
        "submission": submission,
    }
    sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
