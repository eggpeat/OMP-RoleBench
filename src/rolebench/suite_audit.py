"""Audit engine evaluating the task suite against the v1 freeze profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .contracts import (
    BUILTIN_ROLES,
    ContractError,
    canonical_json,
    discover_root,
    load_repository,
    resolve_root,
)
from .pool_routing import load_pool_lane_registry
from .routing_topology import load_routing_topology


@dataclass(frozen=True)
class CapabilityAudit:
    capability: str
    covered: bool
    anchors: list[str]


@dataclass(frozen=True)
class RoleAuditResult:
    role: str
    min_anchor_count: int
    actual_anchor_count: int
    anchors: list[str]
    required_capabilities: list[str]
    covered_capabilities: list[str]
    missing_capabilities: list[str]
    structurally_ready: bool
    status: str
    routing_eligible: bool
    reason_codes: list[str]


@dataclass(frozen=True)
class LaneAuditResult:
    lane: str
    role: str
    min_anchor_count: int
    actual_anchor_count: int
    anchors: list[str]
    required_capabilities: list[str]
    covered_capabilities: list[str]
    missing_capabilities: list[str]
    structurally_ready: bool
    reason_codes: list[str]


@dataclass(frozen=True)
class SuiteAuditReport:
    schema_version: str
    profile_id: str
    profile_version: str
    profile_status: str
    total_roles: int
    structurally_ready_roles: int
    total_task_lanes: int
    structurally_ready_lanes: int
    total_anchors: int
    role_results: dict[str, RoleAuditResult]
    lane_results: dict[str, LaneAuditResult]
    global_reason_codes: list[str]
    structurally_frozen: bool
    routing_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "profile_status": self.profile_status,
            "total_roles": self.total_roles,
            "structurally_ready_roles": self.structurally_ready_roles,
            "total_task_lanes": self.total_task_lanes,
            "structurally_ready_lanes": self.structurally_ready_lanes,
            "total_anchors": self.total_anchors,
            "role_results": {k: asdict(v) for k, v in self.role_results.items()},
            "lane_results": {k: asdict(v) for k, v in self.lane_results.items()},
            "global_reason_codes": self.global_reason_codes,
            "structurally_frozen": self.structurally_frozen,
            "routing_eligible": self.routing_eligible,
        }


def audit_task_suite(root: Path | None = None) -> SuiteAuditReport:
    """Audit repository task packs and tasks against the task suite profile."""
    resolved_root = resolve_root(root)
    profile_path = resolved_root / "contracts/task-suite-profile-v1.json"
    if not profile_path.exists():
        raise ContractError("task suite profile not found", "contracts/task-suite-profile-v1.json")

    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    repo = load_repository(resolved_root)
    topology = load_routing_topology(resolved_root)
    lane_registry = load_pool_lane_registry(resolved_root)

    profile_roles = profile.get("roles", {})
    profile_lanes = profile.get("task_lanes", {})

    role_results: dict[str, RoleAuditResult] = {}
    total_anchors = 0

    # Collect task pack details
    pack_by_role: dict[str, dict[str, Any]] = {}
    for role_name, pack in repo.task_packs:
        pack_by_role[role_name] = pack

    for role_name in BUILTIN_ROLES:
        role_req = profile_roles.get(role_name, {})
        min_anchors = int(role_req.get("min_anchor_count", 1))
        required_caps = list(role_req.get("required_capabilities", []))

        pack = pack_by_role.get(role_name)
        actual_anchors: list[str] = []
        covered_caps: set[str] = set()
        pack_status = str(pack.get("status", "authoring")) if pack else "missing"
        routing_eligible = bool(pack.get("routing_eligible", False)) if pack else False

        if pack and isinstance(pack.get("entries"), list):
            for entry in pack["entries"]:
                if not isinstance(entry, dict):
                    continue
                task_ref = entry.get("task")
                if isinstance(task_ref, dict) and isinstance(task_ref.get("path"), str):
                    task_path = resolved_root / task_ref["path"]
                    if task_path.exists():
                        with open(task_path, "r", encoding="utf-8") as tf:
                            task_doc = json.load(tf)
                            actual_anchors.append(str(task_doc.get("task_id", task_path.stem)))
                            tags = task_doc.get("capability_tags", [])
                            if isinstance(tags, list):
                                covered_caps.update(str(t) for t in tags)

        total_anchors += len(actual_anchors)
        missing_caps = [c for c in required_caps if c not in covered_caps]
        covered_list = sorted(covered_caps)

        reasons: list[str] = []
        anchor_ok = len(actual_anchors) >= min_anchors
        caps_ok = len(missing_caps) == 0

        if not anchor_ok:
            reasons.append("ROLE_ANCHORS_DEFICIT")
        if not caps_ok:
            reasons.append("ROLE_CAPABILITY_GAP")
        if anchor_ok and caps_ok:
            reasons.append("ROLE_STRUCTURALLY_READY")

        if not routing_eligible:
            reasons.append("CALIBRATION_REQUIRED")

        struct_ready = anchor_ok and caps_ok

        role_results[role_name] = RoleAuditResult(
            role=role_name,
            min_anchor_count=min_anchors,
            actual_anchor_count=len(actual_anchors),
            anchors=actual_anchors,
            required_capabilities=required_caps,
            covered_capabilities=covered_list,
            missing_capabilities=missing_caps,
            structurally_ready=struct_ready,
            status=pack_status,
            routing_eligible=routing_eligible,
            reason_codes=reasons,
        )

    # Collect task lane details (only for role == 'task')
    task_pack = pack_by_role.get("task")
    lane_anchors: dict[str, list[str]] = {lane: [] for lane in profile_lanes}
    lane_caps: dict[str, set[str]] = {lane: set() for lane in profile_lanes}

    if task_pack and isinstance(task_pack.get("entries"), list):
        for entry in task_pack["entries"]:
            if not isinstance(entry, dict):
                continue
            entry_lane = entry.get("routing_lane")
            task_ref = entry.get("task")
            if isinstance(task_ref, dict) and isinstance(task_ref.get("path"), str):
                task_path = resolved_root / task_ref["path"]
                if task_path.exists():
                    with open(task_path, "r", encoding="utf-8") as tf:
                        task_doc = json.load(tf)
                        effective_lane = entry_lane or task_doc.get("routing_lane")
                        if effective_lane and effective_lane in lane_anchors:
                            lane_anchors[effective_lane].append(str(task_doc.get("task_id", task_path.stem)))
                            tags = task_doc.get("capability_tags", [])
                            if isinstance(tags, list):
                                lane_caps[effective_lane].update(str(t) for t in tags)

    lane_results: dict[str, LaneAuditResult] = {}
    for lane_id, lane_req in profile_lanes.items():
        min_anchors = int(lane_req.get("min_anchor_count", 2))
        required_caps = list(lane_req.get("required_capabilities", []))
        anchors = lane_anchors.get(lane_id, [])
        covered = sorted(lane_caps.get(lane_id, set()))
        missing = [c for c in required_caps if c not in covered]

        anchor_ok = len(anchors) >= min_anchors
        caps_ok = len(missing) == 0
        struct_ready = anchor_ok and caps_ok

        reasons: list[str] = []
        if not anchor_ok:
            reasons.append("LANE_ANCHORS_DEFICIT")
        if not caps_ok:
            reasons.append("LANE_CAPABILITY_GAP")
        if struct_ready:
            reasons.append("LANE_STRUCTURALLY_READY")

        lane_results[lane_id] = LaneAuditResult(
            lane=lane_id,
            role="task",
            min_anchor_count=min_anchors,
            actual_anchor_count=len(anchors),
            anchors=anchors,
            required_capabilities=required_caps,
            covered_capabilities=covered,
            missing_capabilities=missing,
            structurally_ready=struct_ready,
            reason_codes=reasons,
        )

    ready_roles = sum(1 for r in role_results.values() if r.structurally_ready)
    ready_lanes = sum(1 for l in lane_results.values() if l.structurally_ready)

    global_reasons: list[str] = []
    if ready_roles < len(BUILTIN_ROLES) or ready_lanes < len(profile_lanes):
        global_reasons.append("STRUCTURAL_FREEZE_INCOMPLETE")
    else:
        global_reasons.append("SUITE_STRUCTURALLY_READY")

    all_packs_routing = all(r.routing_eligible for r in role_results.values())
    if not all_packs_routing:
        global_reasons.append("SUITE_CALIBRATION_REQUIRED")
        global_reasons.append("SUITE_ROUTING_INELIGIBLE")

    struct_frozen = (ready_roles == len(BUILTIN_ROLES)) and (ready_lanes == len(profile_lanes))

    return SuiteAuditReport(
        schema_version="omp.task-suite-audit-report/v1",
        profile_id=str(profile.get("profile_id", "omp-rolebench-v1-task-suite")),
        profile_version=str(profile.get("version", "1.0.0")),
        profile_status=str(profile.get("status", "draft")),
        total_roles=len(BUILTIN_ROLES),
        structurally_ready_roles=ready_roles,
        total_task_lanes=len(profile_lanes),
        structurally_ready_lanes=ready_lanes,
        total_anchors=total_anchors,
        role_results=role_results,
        lane_results=lane_results,
        global_reason_codes=global_reasons,
        structurally_frozen=struct_frozen,
        routing_eligible=all_packs_routing,
    )


def format_suite_audit_human(report: SuiteAuditReport) -> str:
    """Render human-readable suite audit summary."""
    lines: list[str] = [
        f"=== OMP RoleBench Task Suite v1 Audit ===",
        f"Profile: {report.profile_id} (v{report.profile_version}) [{report.profile_status}]",
        f"Roles Structurally Ready: {report.structurally_ready_roles}/{report.total_roles}",
        f"Task Lanes Structurally Ready: {report.structurally_ready_lanes}/{report.total_task_lanes}",
        f"Total Qualified Anchors: {report.total_anchors}",
        f"Global Status: {', '.join(report.global_reason_codes)}",
        "",
        "--- Role Coverage Matrix ---",
    ]

    for role_name, res in sorted(report.role_results.items()):
        status_symbol = "✓" if res.structurally_ready else "✗"
        lines.append(
            f"  {status_symbol} @{role_name:<10} anchors={res.actual_anchor_count}/{res.min_anchor_count} "
            f"caps={len(res.covered_capabilities)}/{len(res.required_capabilities)} "
            f"status={res.status} routing={res.routing_eligible} [{', '.join(res.reason_codes)}]"
        )
        if res.missing_capabilities:
            lines.append(f"      missing capabilities: {', '.join(res.missing_capabilities)}")

    lines.append("")
    lines.append("--- Task Lane Specialization Matrix (@task) ---")
    for lane_id, lres in sorted(report.lane_results.items()):
        status_symbol = "✓" if lres.structurally_ready else "✗"
        lines.append(
            f"  {status_symbol} {lane_id:<24} anchors={lres.actual_anchor_count}/{lres.min_anchor_count} "
            f"caps={len(lres.covered_capabilities)}/{len(lres.required_capabilities)} [{', '.join(lres.reason_codes)}]"
        )
        if lres.missing_capabilities:
            lines.append(f"      missing capabilities: {', '.join(lres.missing_capabilities)}")

    return "\n".join(lines)
