"""Task lanes and weighted-selection policy validation for OMP RoleBench.

All native OMP roles may use weighted selection. Retry fallback chains are a
separate recovery layer and are required for weighted allocations just as they
are for configured-primary routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Mapping, Sequence

import hashlib
import math
from jsonschema import Draft202012Validator, FormatChecker

from .contracts import ContractError, resolve_root
from .routing_topology import ROUTING_ROLES, RoutingTopology, load_routing_topology

_LANE_REGISTRY_FILE = Path("contracts/pool-lane-registry.json")
_LANE_REGISTRY_SCHEMA = Path("contracts/schemas/pool-lane-registry.schema.json")
_POOL_POLICY_SCHEMA = Path("contracts/schemas/pool-policy.schema.json")


@dataclass(frozen=True)
class PoolLane:
    lane_id: str
    role: str
    slug: str
    name: str
    description: str
    routing_traits: tuple[str, ...]
    complexity: str
    min_direct_samples: int
    minimum_regret_reduction: float
    status: str


@dataclass(frozen=True)
class PoolLaneRegistry:
    registry_id: str
    lanes: Mapping[str, PoolLane]

    def get(self, role: str, lane: str | None) -> PoolLane | None:
        if lane is None:
            return None
        return self.lanes.get(normalize_lane_id(role, lane))


def _load_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ContractError(str(error), str(path)) from error
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
            str(path),
        ) from error
    if not isinstance(value, dict):
        raise ContractError("document must be a JSON object", str(path))
    return value


def _schema_validate(instance: Mapping[str, object], schema: Mapping[str, object], *, label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    json_path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
    )
    raise ContractError(error.message, label, json_path)


def normalize_lane_id(role: str, lane: str) -> str:
    if role != "task":
        raise ContractError("v1 routing lanes are supported only for the task role")
    candidate = lane.strip()
    if not candidate:
        raise ContractError("routing lane must not be empty")
    if "/" not in candidate:
        candidate = f"{role}/{candidate}"
    owner, separator, slug = candidate.partition("/")
    if not separator or owner != role or not slug or "/" in slug:
        raise ContractError(f"routing lane {candidate!r} does not belong to role {role!r}")
    return candidate


def load_pool_lane_registry(root: Path | None = None) -> PoolLaneRegistry:
    resolved = resolve_root(root)
    document = _load_object(resolved / _LANE_REGISTRY_FILE)
    schema = _load_object(resolved / _LANE_REGISTRY_SCHEMA)
    _schema_validate(document, schema, label=_LANE_REGISTRY_FILE.as_posix())
    values = document.get("lanes")
    if not isinstance(values, dict):
        raise ContractError("lanes must be an object", _LANE_REGISTRY_FILE.as_posix(), "$.lanes")
    lanes: dict[str, PoolLane] = {}
    for lane_id in sorted(values):
        value = values[lane_id]
        if not isinstance(lane_id, str) or not isinstance(value, dict):
            raise ContractError("lane registry entries must map lane IDs to objects")
        role = value.get("role")
        if not isinstance(role, str) or normalize_lane_id(role, lane_id) != lane_id:
            raise ContractError("lane ID and role ownership do not match", _LANE_REGISTRY_FILE.as_posix())
        inheritance = value.get("inheritance")
        traits = value.get("routing_traits")
        if not isinstance(inheritance, dict) or not isinstance(traits, list):
            raise ContractError("invalid pool lane structure", _LANE_REGISTRY_FILE.as_posix())
        lanes[lane_id] = PoolLane(
            lane_id=lane_id,
            role=role,
            slug=lane_id.split("/", 1)[1],
            name=str(value["name"]),
            description=str(value["description"]),
            routing_traits=tuple(str(item) for item in traits),
            complexity=str(value["complexity"]),
            min_direct_samples=int(inheritance["min_direct_samples"]),
            minimum_regret_reduction=float(inheritance["minimum_regret_reduction"]),
            status=str(value["status"]),
        )
    registry_id = document.get("registry_id")
    if not isinstance(registry_id, str):
        raise ContractError("registry_id must be a string", _LANE_REGISTRY_FILE.as_posix())
    return PoolLaneRegistry(registry_id=registry_id, lanes=lanes)


def should_specialize(lane: PoolLane, *, direct_sample_count: int, estimated_regret_reduction: float) -> bool:
    return (
        lane.status == "active"
        and direct_sample_count >= lane.min_direct_samples
        and estimated_regret_reduction >= lane.minimum_regret_reduction
    )


def _validate_route_list(routes: object, *, label: str) -> None:
    if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes)):
        raise ContractError("routes must be an array", label)
    seen: set[str] = set()
    total = 0
    for index, item in enumerate(routes):
        if not isinstance(item, Mapping):
            raise ContractError("route entry must be an object", label, f"$.routes[{index}]")
        route_id = item.get("route_id")
        weight = item.get("weight_bps")
        if not isinstance(route_id, str) or not isinstance(weight, int):
            raise ContractError("route_id and integer weight_bps are required", label, f"$.routes[{index}]")
        if route_id in seen:
            raise ContractError(f"duplicate route_id {route_id!r}", label, f"$.routes[{index}].route_id")
        seen.add(route_id)
        total += weight
    if total != 10_000:
        raise ContractError(f"route weights must sum to 10000 bps, got {total}", label, "$.routes")


def _validate_fallback_chain(chain: object, *, label: str) -> None:
    if not isinstance(chain, Sequence) or isinstance(chain, (str, bytes)) or len(chain) == 0:
        raise ContractError("fallback_chain must be a non-empty array", label)
    if not all(isinstance(item, str) and item.strip() for item in chain):
        raise ContractError("fallback_chain entries must be non-empty selector strings", label)
    if len(set(chain)) != len(chain):
        raise ContractError("fallback_chain entries must be unique", label)


def _parse_time(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError("timestamp must be a string", label)
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ContractError("timestamp must be valid RFC 3339", label) from error
    if parsed.tzinfo is None:
        raise ContractError("timestamp must include an offset", label)
    return parsed


def validate_pool_policy(
    policy: Mapping[str, object], *, root: Path | None = None,
    topology: RoutingTopology | None = None, lanes: PoolLaneRegistry | None = None,
) -> None:
    """Validate weighted selection, retry fallbacks, task lanes, and inheritance."""
    resolved = resolve_root(root)
    _schema_validate(policy, _load_object(resolved / _POOL_POLICY_SCHEMA), label="omp.pool-policy/v1")
    active_topology = topology or load_routing_topology(resolved)
    active_lanes = lanes or load_pool_lane_registry(resolved)
    topology_reference = policy.get("routing_topology")
    lane_reference = policy.get("lane_registry")
    if not isinstance(topology_reference, Mapping) or topology_reference.get("topology_id") != active_topology.topology_id:
        raise ContractError("policy must bind the active routing topology", "omp.pool-policy/v1", "$.routing_topology")
    if not isinstance(lane_reference, Mapping) or lane_reference.get("registry_id") != active_lanes.registry_id:
        raise ContractError("policy must bind the active pool lane registry", "omp.pool-policy/v1", "$.lane_registry")
    created = _parse_time(policy.get("created_at"), label="$.created_at")
    valid_from = _parse_time(policy.get("valid_from"), label="$.valid_from")
    valid_until = _parse_time(policy.get("valid_until"), label="$.valid_until")
    if not created <= valid_from < valid_until:
        raise ContractError("timestamps must satisfy created_at <= valid_from < valid_until", "omp.pool-policy/v1")
    pools = policy.get("pools")
    if not isinstance(pools, Mapping):
        raise ContractError("pools must be an object", "omp.pool-policy/v1", "$.pools")
    for role, pool in pools.items():
        if role not in ROUTING_ROLES or not active_topology.supports_strategy(str(role), "weighted"):
            raise ContractError(f"role {role!r} does not support weighted selection", "omp.pool-policy/v1", f"$.pools.{role}")
        if not isinstance(pool, Mapping):
            raise ContractError("pool must be an object", "omp.pool-policy/v1")
        default = pool.get("default")
        if not isinstance(default, Mapping):
            raise ContractError("pool default allocation is required", "omp.pool-policy/v1")
        _validate_route_list(default.get("routes"), label=f"omp.pool-policy/v1:{role}:default")
        _validate_fallback_chain(default.get("fallback_chain"), label=f"omp.pool-policy/v1:{role}:default")
        lane_values = pool.get("lanes")
        if lane_values is None:
            continue
        if role != "task":
            raise ContractError("only task may define routing lanes in v1", "omp.pool-policy/v1", f"$.pools.{role}.lanes")
        if not isinstance(lane_values, Mapping):
            raise ContractError("task lanes must be an object", "omp.pool-policy/v1")
        for slug, allocation in lane_values.items():
            if not isinstance(slug, str) or not isinstance(allocation, Mapping):
                raise ContractError("task lanes must map slugs to allocation objects")
            lane_id = normalize_lane_id("task", slug)
            lane = active_lanes.lanes.get(lane_id)
            if lane is None:
                raise ContractError(f"policy references unknown routing lane {lane_id!r}", "omp.pool-policy/v1")
            _validate_route_list(allocation.get("routes"), label=f"omp.pool-policy/v1:{lane_id}")
            _validate_fallback_chain(allocation.get("fallback_chain"), label=f"omp.pool-policy/v1:{lane_id}")
            evidence = allocation.get("evidence")
            if not isinstance(evidence, Mapping):
                raise ContractError("lane evidence is required", "omp.pool-policy/v1")
            specialized = evidence.get("status") == "specialized"
            direct_samples = evidence.get("direct_sample_count")
            regret = evidence.get("estimated_regret_reduction")
            if specialized:
                if not isinstance(direct_samples, int) or not isinstance(regret, (int, float)) or not should_specialize(
                    lane, direct_sample_count=direct_samples, estimated_regret_reduction=float(regret)
                ):
                    raise ContractError(f"lane {lane_id!r} lacks evidence required for specialization", "omp.pool-policy/v1")
            else:
                if allocation.get("routes") != default.get("routes") or allocation.get("fallback_chain") != default.get("fallback_chain"):
                    raise ContractError(f"non-specialized lane {lane_id!r} must inherit task default routes and fallback_chain exactly", "omp.pool-policy/v1")


def resolve_pool_allocation(
    policy: Mapping[str, object], *, role: str, lane: str | None = None,
    registry: PoolLaneRegistry | None = None,
) -> Mapping[str, object]:
    if policy.get("schema_version") != "omp.pool-policy/v1":
        raise ContractError("weighted resolution requires omp.pool-policy/v1")
    if role not in ROUTING_ROLES:
        raise ContractError(f"unknown routing role {role!r}")
    pools = policy.get("pools")
    if not isinstance(pools, Mapping):
        raise ContractError("pool policy pools must be an object")
    pool = pools.get(role)
    if not isinstance(pool, Mapping):
        raise ContractError(f"pool policy has no weighted allocation for role {role!r}")
    default = pool.get("default")
    if not isinstance(default, Mapping):
        raise ContractError(f"pool {role!r} has no default allocation")
    if lane is None or role != "task":
        return default
    lane_id = normalize_lane_id(role, lane)
    if registry is not None and lane_id not in registry.lanes:
        return default
    lane_values = pool.get("lanes")
    if not isinstance(lane_values, Mapping):
        return default
    allocation = lane_values.get(lane_id.split("/", 1)[1])
    if not isinstance(allocation, Mapping):
        return default
    evidence = allocation.get("evidence")
    if not isinstance(evidence, Mapping) or evidence.get("status") != "specialized":
        return default
    return allocation


def weighted_rendezvous_rank(
    routes: Sequence[Mapping[str, object]],
    *,
    routing_key: str,
    role: str,
    policy_checksum: str,
    seed: str = "",
) -> list[str]:
    """Compute deterministic weighted rendezvous ranking for a set of candidate routes."""
    scores: list[tuple[float, str]] = []
    for item in routes:
        route_id = str(item["route_id"])
        weight_bps = int(item.get("weight_bps", 10_000))
        key = f"{policy_checksum}:{seed}:{role}:{routing_key}:{route_id}".encode("utf-8")
        digest = hashlib.sha256(key).digest()
        uniform_val = int.from_bytes(digest[:8], "big") / (1 << 64)
        uniform_val = max(1e-12, min(1.0 - 1e-12, uniform_val))
        score = -weight_bps / math.log(uniform_val)
        scores.append((score, route_id))
    scores.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
    return [route_id for _, route_id in scores]


def build_normative_recovery_chain(
    selected_route: str,
    ranked_candidates: Sequence[str],
    fallback_chain: Sequence[str],
) -> list[str]:
    """Build the deduplicated normative recovery sequence for a selected route."""
    seen = {selected_route}
    chain: list[str] = []
    for candidate in ranked_candidates:
        if candidate not in seen:
            seen.add(candidate)
            chain.append(candidate)
    for fallback in fallback_chain:
        if fallback not in seen:
            seen.add(fallback)
            chain.append(fallback)
    return chain
