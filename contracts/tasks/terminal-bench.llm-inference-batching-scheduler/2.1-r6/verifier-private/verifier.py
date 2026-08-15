#!/usr/bin/env python3
"""Strict passive verifier for LLM inference batching scheduler plan runner evidence."""

from __future__ import annotations

import collections
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from typing import Any, NoReturn

RUNNER_EVIDENCE_MAGIC: bytes = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION: str = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION: str = "omp.verifier-result/v1"
MAX_EVIDENCE_BYTES: int = 32 * 1024 * 1024

HEADER_KEYS: set[str] = {
    "schema_version",
    "attempt_nonce",
    "run_id",
    "task_digest_sha256",
    "policy_digest_sha256",
    "artifact_digest_sha256",
    "evaluation_request_digest_sha256",
    "verifier_image_digest_sha256",
    "runner",
    "container",
    "stdout",
    "stderr",
}

CONTAINER_KEYS: set[str] = {
    "container_id",
    "state",
    "exit_code",
    "oom_killed",
    "timed_out",
    "overflowed",
    "duration_seconds",
    "removed",
}

RUNNER_KEYS: set[str] = {"image", "config_digest_sha256", "platform", "argv"}
IO_KEYS: set[str] = {"byte_count", "digest_sha256", "authority"}
SNAPSHOT_KEYS: set[str] = {"schema_version", "status", "error", "submission"}

PHASE_OPERATION_MAP: dict[str, str] = {
    "READ_INPUTS": "INGEST_AND_VALIDATE",
    "SYNTHESIZE_GLOBAL_SHAPES": "GLOBAL_SHAPE_ALLOCATION",
    "ASSIGN_BATCHES": "BUCKET_BATCH_PACKING",
    "EVALUATE_GATES": "COST_AND_SLA_VALIDATION",
    "COMMIT_OUTPUTS": "ATOMIC_COMMIT_AND_ROLLBACK",
    "ROLLBACK_ON_FAILURE": "ATOMIC_COMMIT_AND_ROLLBACK",
}

VALID_INVARIANT_SCOPES: dict[str, set[str]] = {
    "EXACT_ONCE_ASSIGNMENT": {"CROSS_BUCKET", "PER_BUCKET", "GLOBAL"},
    "TENSOR_SHAPE_ALIGNMENT": {"GLOBAL", "PER_BUCKET"},
    "GLOBAL_SHAPE_BUDGET_LE_8": {"CROSS_BUCKET", "GLOBAL"},
    "COST_AND_LATENCY_GATES": {"GLOBAL", "CROSS_BUCKET"},
    "IMMUTABLE_INPUTS": {"STORAGE", "GLOBAL"},
    "ATOMIC_TRANSACTION_OR_ROLLBACK": {"STORAGE", "GLOBAL"},
    "DETERMINISTIC_TIE_BREAKING": {"GLOBAL", "CROSS_BUCKET", "PER_BUCKET"},
}


class EvidenceError(ValueError):
    """Malformed runner evidence payload."""


def _reject_constant(value: str) -> NoReturn:
    raise EvidenceError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _emit_result(
    *,
    outcome: str,
    reward: int | None,
    run_id: str,
    attempt_nonce: str,
    artifact_digest_sha256: str,
    runner_evidence_digest_sha256: str,
    evaluation_request_digest_sha256: str,
    verifier_image_digest_sha256: str,
) -> int:
    result = {
        "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
        "outcome": outcome,
        "reward": reward,
        "run_id": run_id,
        "attempt_nonce": attempt_nonce,
        "artifact_digest_sha256": artifact_digest_sha256,
        "runner_evidence_digest_sha256": runner_evidence_digest_sha256,
        "evaluation_request_digest_sha256": evaluation_request_digest_sha256,
        "verifier_image_digest_sha256": verifier_image_digest_sha256,
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _find_ancestors(step_id: str, step_deps: dict[str, list[str]]) -> set[str]:
    ancestors: set[str] = set()
    queue = collections.deque(step_deps.get(step_id, []))
    while queue:
        curr = queue.popleft()
        if curr not in ancestors:
            ancestors.add(curr)
            for dep in step_deps.get(curr, []):
                if dep not in ancestors:
                    queue.append(dep)
    return ancestors


def _find_descendants(step_id: str, adj: dict[str, list[str]]) -> set[str]:
    descendants: set[str] = set()
    queue = collections.deque(adj.get(step_id, []))
    while queue:
        curr = queue.popleft()
        if curr not in descendants:
            descendants.add(curr)
            for nxt in adj.get(curr, []):
                if nxt not in descendants:
                    queue.append(nxt)
    return descendants


def _is_dag(step_ids: list[str], step_deps: dict[str, list[str]]) -> bool:
    in_degree: dict[str, int] = {s: 0 for s in step_ids}
    adj: dict[str, list[str]] = collections.defaultdict(list)
    for s_id, deps in step_deps.items():
        for d in deps:
            adj[d].append(s_id)
            in_degree[s_id] += 1

    queue = collections.deque([s for s in step_ids if in_degree[s] == 0])
    visited_count = 0
    while queue:
        curr = queue.popleft()
        visited_count += 1
        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return visited_count == len(step_ids)


def _verify_plan(submission: dict[str, Any], expected: dict[str, Any]) -> bool:
    if submission.get("schema_version") != "rolebench.scheduler-plan/v1":
        return False
    if submission.get("task_domain") != expected.get("task_domain"):
        return False
    if submission.get("service_level_model") != expected.get("required_service_level_model"):
        return False

    arch = submission.get("architecture", {})
    components = arch.get("components", [])
    interfaces = arch.get("interfaces", [])
    graph = submission.get("execution_graph", {})
    steps = graph.get("steps", [])
    invariants = submission.get("invariants", [])
    rm = submission.get("risks_and_mitigations", {})
    risks = rm.get("risks", [])
    gates = rm.get("gates", [])
    rollout = submission.get("rollout_and_recovery", {})

    if not components or not interfaces or not steps or not invariants or not risks or not gates:
        return False

    identity_fields = (
        (components, "component_id"),
        (interfaces, "interface_id"),
        (steps, "step_id"),
        (invariants, "invariant_id"),
        (risks, "risk_id"),
        (gates, "gate_id"),
    )
    for items, identity_field in identity_fields:
        identities = [item[identity_field] for item in items]
        if len(identities) != len(set(identities)):
            return False

    step_ids = [s["step_id"] for s in steps]
    step_map = {s["step_id"]: s for s in steps}
    step_deps = {s["step_id"]: s.get("depends_on", []) for s in steps}

    # 1. Acyclicity Check
    if not _is_dag(step_ids, step_deps):
        return False

    # 2. Build DAG Ancestors and Descendants
    adj: dict[str, list[str]] = collections.defaultdict(list)
    for s_id, deps in step_deps.items():
        for d in deps:
            adj[d].append(s_id)

    ancestors_map = {s_id: _find_ancestors(s_id, step_deps) for s_id in step_ids}
    descendants_map = {s_id: _find_descendants(s_id, adj) for s_id in step_ids}

    # 3. Phase and Operation Kind Mapping & Expected Phases
    phases_present = {s.get("phase") for s in steps}
    expected_phases = set(expected.get("required_phases", []))
    if not expected_phases.issubset(phases_present):
        return False
    operations_present = {s.get("operation_kind") for s in steps}
    expected_operations = set(expected.get("required_operation_kinds", []))
    if operations_present != expected_operations:
        return False


    for s in steps:
        op = s.get("operation_kind")
        phase = s.get("phase")
        if op not in PHASE_OPERATION_MAP or PHASE_OPERATION_MAP[op] != phase:
            return False

    # 4. Categorize Steps by Operation Kind
    read_steps = [s for s in steps if s.get("operation_kind") == "READ_INPUTS"]
    shape_steps = [s for s in steps if s.get("operation_kind") == "SYNTHESIZE_GLOBAL_SHAPES"]
    pack_steps = [s for s in steps if s.get("operation_kind") == "ASSIGN_BATCHES"]
    gate_steps = [s for s in steps if s.get("operation_kind") == "EVALUATE_GATES"]
    commit_steps = [s for s in steps if s.get("operation_kind") == "COMMIT_OUTPUTS"]
    rollback_steps = [s for s in steps if s.get("operation_kind") == "ROLLBACK_ON_FAILURE"]

    if not read_steps or not shape_steps or not pack_steps or not gate_steps or not commit_steps or not rollback_steps:
        return False

    # Semantic Ordering 1: All read steps must be ancestors of all shape synthesis steps
    for s_step in shape_steps:
        s_anc = ancestors_map[s_step["step_id"]]
        for r_step in read_steps:
            if r_step["step_id"] not in s_anc:
                return False

    # Semantic Ordering 2: All shape synthesis steps must be ancestors of all packing steps
    for p_step in pack_steps:
        p_anc = ancestors_map[p_step["step_id"]]
        for s_step in shape_steps:
            if s_step["step_id"] not in p_anc:
                return False

    # Semantic Ordering 3: All packing steps must be ancestors of all gate evaluation steps
    for g_step in gate_steps:
        g_anc = ancestors_map[g_step["step_id"]]
        for p_step in pack_steps:
            if p_step["step_id"] not in g_anc:
                return False

    # Semantic Ordering 4: All gate evaluation steps must be ancestors of all commit steps
    for c_step in commit_steps:
        c_anc = ancestors_map[c_step["step_id"]]
        for g_step in gate_steps:
            if g_step["step_id"] not in c_anc:
                return False

    # 5. Connected Interface Dataflow Across Dependency Edges & Typed Operation Contracts
    comp_used: set[str] = set()
    iface_producers: dict[str, str] = {}
    iface_consumers: dict[str, set[str]] = collections.defaultdict(set)
    declared_comp_ids = {c["component_id"] for c in components}
    declared_iface_ids = {i["interface_id"] for i in interfaces}

    for s in steps:
        comp_used.add(s["component_id"])
        for out_id in s.get("outputs", []):
            if out_id not in declared_iface_ids or out_id in iface_producers:
                # Every output is declared and each interface has one producer.
                return False
            iface_producers[out_id] = s["step_id"]
        for in_id in s.get("inputs", []):
            if in_id not in declared_iface_ids:
                return False
            iface_consumers[in_id].add(s["step_id"])

    # All declared components must be used
    if declared_comp_ids != comp_used:
        return False

    # All declared interfaces must have a producer step
    if set(iface_producers.keys()) != declared_iface_ids:
        return False

    iface_map = {iface["interface_id"]: iface for iface in interfaces}
    interface_kinds = [iface["interface_kind"] for iface in interfaces]
    expected_iface_kinds = set(expected.get("required_interface_kinds", []))
    if len(interface_kinds) != len(set(interface_kinds)) or set(interface_kinds) != expected_iface_kinds:
        return False
    iface_kind_map = {iface["interface_kind"]: iface for iface in interfaces}
    staged_b1_id = iface_kind_map["STAGED_BATCH_PLANS_B1"]["interface_id"]
    staged_b2_id = iface_kind_map["STAGED_BATCH_PLANS_B2"]["interface_id"]
    required_producer_operations = {
        "RAW_REQUEST_FEEDS": "READ_INPUTS",
        "GLOBAL_SHAPE_CATALOG": "SYNTHESIZE_GLOBAL_SHAPES",
        "STAGED_BATCH_PLANS_B1": "ASSIGN_BATCHES",
        "STAGED_BATCH_PLANS_B2": "ASSIGN_BATCHES",
        "GATE_VERDICT_REPORT": "EVALUATE_GATES",
        "COMMITTED_PLAN_MANIFESTS": "COMMIT_OUTPUTS",
    }
    for interface_kind, required_operation in required_producer_operations.items():
        interface_id = iface_kind_map[interface_kind]["interface_id"]
        producer_step_id = iface_producers[interface_id]
        if step_map[producer_step_id]["operation_kind"] != required_operation:
            return False

    verdict_id = iface_kind_map["GATE_VERDICT_REPORT"]["interface_id"]
    required_publication_inputs = {staged_b1_id, staged_b2_id, verdict_id}


    # Verify interface producer component binding and consumer component binding
    for s in steps:
        comp_id = s.get("component_id")
        for out_id in s.get("outputs", []):
            if iface_map[out_id]["producer_component_id"] != comp_id:
                return False
        for in_id in s.get("inputs", []):
            if comp_id not in iface_map[in_id]["consumer_component_ids"]:
                return False

    # Verify each step input is produced by an ancestor step in the DAG
    for s in steps:
        s_anc = ancestors_map[s["step_id"]]
        for in_id in s.get("inputs", []):
            prod_step = iface_producers.get(in_id)
            if not prod_step or prod_step not in s_anc:
                return False

    # Commit steps must publish the staged plans only after consuming the gate verdict.
    terminal_outputs = set()
    for c_step in commit_steps:
        if not required_publication_inputs.issubset(set(c_step.get("inputs", []))):
            return False
        terminal_outputs.update(c_step.get("outputs", []))

    if not terminal_outputs:
        return False

    # Non-terminal interfaces must name exactly the components that consume them.
    for iface_id, iface in iface_map.items():
        if iface_id in terminal_outputs:
            if iface["interface_kind"] != "COMMITTED_PLAN_MANIFESTS":
                return False
            continue
        prod_step = iface_producers.get(iface_id)
        cons_steps = iface_consumers.get(iface_id, set())
        if not cons_steps:
            return False
        declared_consumers = set(iface["consumer_component_ids"])
        actual_consumers = {step_map[step_id]["component_id"] for step_id in cons_steps}
        if declared_consumers != actual_consumers:
            return False
        descendants = descendants_map[prod_step]
        if not any(cs in descendants for cs in cons_steps):
            return False

    # 6. Structured Invariant Categories, Semantic Constraints & Exact Reciprocal Bindings
    invariant_categories = [inv["invariant_category"] for inv in invariants]
    expected_inv_cats = set(expected.get("required_invariant_categories", []))
    if (
        len(invariant_categories) != len(set(invariant_categories))
        or set(invariant_categories) != expected_inv_cats
    ):
        return False
    inv_map = {inv["invariant_category"]: inv for inv in invariants}

    step_inv_bindings: dict[str, set[str]] = collections.defaultdict(set)
    for s in steps:
        for inv_id in s.get("enforced_invariants", []):
            step_inv_bindings[inv_id].add(s["step_id"])

    gate_inv_targets: dict[str, set[str]] = collections.defaultdict(set)
    for g in gates:
        gate_inv_targets[g["target_metric_or_invariant"]].add(g["gate_id"])

    for cat, inv in inv_map.items():
        # Validate scope
        valid_scopes = VALID_INVARIANT_SCOPES.get(cat, set())
        if inv.get("scope") not in valid_scopes:
            return False

        inv_id = inv["invariant_id"]
        enf_steps = set(inv.get("enforcing_step_ids", []))
        if not enf_steps:
            return False

        # Reciprocal step binding: enforcing_step_ids must match step enforced_invariants exactly
        bound_steps = step_inv_bindings.get(inv_id, set())
        if enf_steps != bound_steps:
            return False

        # Reciprocal gate binding: verification_gate_ids must EXACTLY equal gates targeting this invariant
        ver_gates = set(inv.get("verification_gate_ids", []))
        targeted_gates = gate_inv_targets.get(inv_id, set())
        if not ver_gates or ver_gates != targeted_gates:
            return False

        # Semantic category-specific enforcing operation kind constraints:
        enf_ops = {step_map[sid]["operation_kind"] for sid in enf_steps}
        if cat == "EXACT_ONCE_ASSIGNMENT":
            if "ASSIGN_BATCHES" not in enf_ops or "EVALUATE_GATES" not in enf_ops:
                return False
        elif cat == "TENSOR_SHAPE_ALIGNMENT":
            if "SYNTHESIZE_GLOBAL_SHAPES" not in enf_ops or "ASSIGN_BATCHES" not in enf_ops:
                return False
        elif cat == "GLOBAL_SHAPE_BUDGET_LE_8":
            if "SYNTHESIZE_GLOBAL_SHAPES" not in enf_ops or "EVALUATE_GATES" not in enf_ops:
                return False
        elif cat == "COST_AND_LATENCY_GATES":
            if "EVALUATE_GATES" not in enf_ops:
                return False
        elif cat == "IMMUTABLE_INPUTS":
            if "READ_INPUTS" not in enf_ops or "ROLLBACK_ON_FAILURE" not in enf_ops:
                return False
        elif cat == "ATOMIC_TRANSACTION_OR_ROLLBACK":
            if "COMMIT_OUTPUTS" not in enf_ops or "ROLLBACK_ON_FAILURE" not in enf_ops:
                return False
        elif cat == "DETERMINISTIC_TIE_BREAKING":
            if "READ_INPUTS" not in enf_ops or "ASSIGN_BATCHES" not in enf_ops:
                return False

    # 7. Structured Quality Gate Categories, Pre-Commit Binding & Specific Target Constraints
    gate_categories = {gate["gate_category"] for gate in gates}
    expected_gate_cats = set(expected.get("required_gate_categories", []))
    if gate_categories != expected_gate_cats:
        return False

    for g in gates:
        if g.get("blocking") is not True:
            return False
        if g.get("fallback_action") not in {"ABORT_AND_ROLLBACK", "FAIL_CLOSED"}:
            return False

        eval_step_id = g.get("evaluation_step_id")
        if not eval_step_id or eval_step_id not in step_map:
            return False
        eval_step = step_map[eval_step_id]
        if eval_step.get("operation_kind") != "EVALUATE_GATES":
            return False

        eval_anc = ancestors_map[eval_step_id]
        preconditions = g.get("precondition_step_ids", [])
        if not preconditions:
            return False

        # Reject self-precondition or non-ancestor precondition
        for pre_id in preconditions:
            if pre_id == eval_step_id or pre_id not in eval_anc:
                return False

        cat = g.get("gate_category")
        pre_ops = {step_map[pid]["operation_kind"] for pid in preconditions}
        target_inv_id = g.get("target_metric_or_invariant")

        if cat == "GLOBAL_SHAPE_BUDGET":
            if "SYNTHESIZE_GLOBAL_SHAPES" not in pre_ops:
                return False
            if target_inv_id not in {inv_map["GLOBAL_SHAPE_BUDGET_LE_8"]["invariant_id"], inv_map["TENSOR_SHAPE_ALIGNMENT"]["invariant_id"]}:
                return False
        elif cat == "REQUEST_INTEGRITY":
            if "ASSIGN_BATCHES" not in pre_ops:
                return False
            if target_inv_id not in {inv_map["EXACT_ONCE_ASSIGNMENT"]["invariant_id"], inv_map["DETERMINISTIC_TIE_BREAKING"]["invariant_id"]}:
                return False
        elif cat == "SLA_AND_COST_BOUNDS":
            if "ASSIGN_BATCHES" not in pre_ops:
                return False
            if target_inv_id != inv_map["COST_AND_LATENCY_GATES"]["invariant_id"]:
                return False
        elif cat == "ATOMIC_WRITE_VERIFICATION":
            if "READ_INPUTS" not in pre_ops or "ASSIGN_BATCHES" not in pre_ops:
                return False
            if target_inv_id not in {inv_map["ATOMIC_TRANSACTION_OR_ROLLBACK"]["invariant_id"], inv_map["IMMUTABLE_INPUTS"]["invariant_id"]}:
                return False

        # Evaluation step must be strict ancestor of all commit steps
        for c_step in commit_steps:
            c_anc = ancestors_map[c_step["step_id"]]
            if eval_step_id not in c_anc:
                return False

    # 8. Structured Atomic Publication & Connected Rollback Guarantees
    commit_strat = rollout.get("atomic_commit_strategy", {})
    if commit_strat.get("strategy_kind") not in {
        "MANIFEST_POINTER_SWAP",
        "GENERATION_DIRECTORY_SWAP",
        "WRITE_AHEAD_LOG_RENAME",
        "TWO_PHASE_TRANSACTION",
    }:
        return False
    if commit_strat.get("cleanup_on_abort") is not True:
        return False

    staging_dir = commit_strat.get("staging_directory", "")
    pub_unit = commit_strat.get("atomic_publication_unit", "")
    if not staging_dir or not pub_unit:
        return False
    if staging_dir not in pub_unit or ("/app/task_file/output_data" not in pub_unit and "current_gen" not in pub_unit):
        return False

    target_paths = set(commit_strat.get("target_paths", []))
    expected_targets = set(expected.get("target_output_files", []))
    if target_paths != expected_targets:
        return False

    input_pres = rollout.get("input_preservation", {})
    immut_paths = set(input_pres.get("immutable_paths", []))
    expected_inputs = set(expected.get("immutable_input_files", []))
    if immut_paths != expected_inputs:
        return False

    roll_guar = rollout.get("rollback_guarantees", {})
    if roll_guar.get("preserves_inputs") is not True:
        return False
    if roll_guar.get("preserves_prior_outputs") is not True:
        return False
    if roll_guar.get("prevents_partial_writes") is not True:
        return False

    roll_step_ids = set(roll_guar.get("rollback_step_ids", []))
    rollback_step_ids_graph = {s["step_id"] for s in rollback_steps}
    if not roll_step_ids or not roll_step_ids.issubset(rollback_step_ids_graph):
        return False

    # Rollback steps must be connected to staged outputs and gate verdicts.

    for r_id in roll_step_ids:
        r_step = step_map[r_id]
        r_anc = ancestors_map[r_id]
        if not any(g_step["step_id"] in r_anc for g_step in gate_steps):
            return False
        r_in = set(r_step.get("inputs", []))
        if not required_publication_inputs.issubset(r_in):
            return False

    # Every declared mitigation must resolve to an executable step in the plan.
    declared_step_ids = set(step_ids)
    for risk in risks:
        mitigation_step_ids = set(risk.get("mitigation_step_ids", []))
        if not mitigation_step_ids or not mitigation_step_ids.issubset(declared_step_ids):
            return False

    # 9. Required Capabilities Coverage
    all_caps: set[str] = set()
    for s in steps:
        all_caps.update(s.get("required_capabilities", []))
    req_caps = set(expected.get("required_capabilities", []))
    if not req_caps.issubset(all_caps):
        return False

    return True


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    evidence_digest = hashlib.sha256(raw).hexdigest()
    fallback = {
        "run_id": "unknown",
        "attempt_nonce": "0" * 64,
        "artifact_digest_sha256": "0" * 64,
        "evaluation_request_digest_sha256": "0" * 64,
        "verifier_image_digest_sha256": "0" * 64,
    }

    def emit(outcome: str, reward: int | None, values: dict[str, str] = fallback) -> int:
        return _emit_result(
            outcome=outcome,
            reward=reward,
            run_id=values["run_id"],
            attempt_nonce=values["attempt_nonce"],
            artifact_digest_sha256=values["artifact_digest_sha256"],
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256=values["evaluation_request_digest_sha256"],
            verifier_image_digest_sha256=values["verifier_image_digest_sha256"],
        )

    if not raw or len(raw) > MAX_EVIDENCE_BYTES:
        return emit("error", None)

    magic_len = len(RUNNER_EVIDENCE_MAGIC)
    if not raw.startswith(RUNNER_EVIDENCE_MAGIC) or len(raw) < magic_len + 8:
        return emit("error", None)

    header_length = struct.unpack(">Q", raw[magic_len : magic_len + 8])[0]
    header_start = magic_len + 8
    header_end = header_start + header_length
    if header_end > len(raw):
        return emit("error", None)

    header_bytes = raw[header_start:header_end]
    try:
        header = json.loads(
            header_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return emit("error", None)

    if (
        not isinstance(header, dict)
        or set(header) != HEADER_KEYS
        or header_bytes != json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        or header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION
        or not isinstance(header.get("run_id"), str)
        or not header.get("run_id")
        or any(
            not _is_sha256(header.get(field))
            for field in (
                "attempt_nonce",
                "task_digest_sha256",
                "policy_digest_sha256",
                "artifact_digest_sha256",
                "evaluation_request_digest_sha256",
                "verifier_image_digest_sha256",
            )
        )
    ):
        return emit("error", None)

    bound = {
        "run_id": str(header["run_id"]),
        "attempt_nonce": str(header["attempt_nonce"]),
        "artifact_digest_sha256": str(header["artifact_digest_sha256"]),
        "evaluation_request_digest_sha256": str(header["evaluation_request_digest_sha256"]),
        "verifier_image_digest_sha256": str(header["verifier_image_digest_sha256"]),
    }

    def reject() -> int:
        return emit("rejected", 0, bound)

    runner_info = header.get("runner")
    if (
        not isinstance(runner_info, dict)
        or set(runner_info) != RUNNER_KEYS
        or not isinstance(runner_info.get("image"), str)
        or not isinstance(runner_info.get("argv"), list)
    ):
        return reject()

    container = header.get("container")
    if not isinstance(container, dict) or set(container) != CONTAINER_KEYS:
        return reject()

    exit_code = container.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
        return reject()

    duration = container.get("duration_seconds")
    if (
        not isinstance(container.get("container_id"), str)
        or not container.get("container_id")
        or container.get("state") != "exited"
        or container.get("oom_killed") is not False
        or container.get("timed_out") is not False
        or container.get("overflowed") is not False
        or container.get("removed") is not True
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        return reject()

    stdout_info = header.get("stdout")
    stderr_info = header.get("stderr")
    if (
        not isinstance(stdout_info, dict)
        or not isinstance(stderr_info, dict)
        or set(stdout_info) != IO_KEYS
        or set(stderr_info) != IO_KEYS
        or stdout_info.get("authority") != "untrusted"
        or stderr_info.get("authority") != "untrusted"
    ):
        return reject()

    stdout_len = stdout_info.get("byte_count")
    stderr_len = stderr_info.get("byte_count")
    if (
        isinstance(stdout_len, bool)
        or isinstance(stderr_len, bool)
        or not isinstance(stdout_len, int)
        or not isinstance(stderr_len, int)
        or stdout_len < 0
        or stderr_len < 0
    ):
        return reject()

    stdout_start = header_end
    stdout_end = stdout_start + stdout_len
    stderr_end = stdout_end + stderr_len
    if stderr_end != len(raw):
        return reject()

    stdout_bytes = raw[stdout_start:stdout_end]
    stderr_bytes = raw[stdout_end:stderr_end]
    if (
        hashlib.sha256(stdout_bytes).hexdigest() != stdout_info.get("digest_sha256")
        or hashlib.sha256(stderr_bytes).hexdigest() != stderr_info.get("digest_sha256")
        or stderr_bytes
    ):
        return reject()

    try:
        snapshot = json.loads(
            stdout_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return reject()

    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != SNAPSHOT_KEYS
        or snapshot.get("schema_version") != "rolebench.scheduler-plan-runner-snapshot/v1"
        or snapshot.get("status") != "applied"
        or snapshot.get("error") is not None
    ):
        return reject()

    submission = snapshot.get("submission")
    if not isinstance(submission, dict):
        return reject()

    expected_path = Path(__file__).parent / "expected_plan_graph.json"
    try:
        with open(expected_path, "r", encoding="utf-8") as f:
            expected = json.load(f)
    except Exception:
        return emit("error", None, bound)

    try:
        verified = _verify_plan(submission, expected)
    except (KeyError, TypeError, ValueError):
        return reject()
    if not verified:
        return reject()

    return emit("accepted", 1, bound)


if __name__ == "__main__":
    raise SystemExit(main())
