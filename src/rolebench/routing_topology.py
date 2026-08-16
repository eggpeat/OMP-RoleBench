"""Authoritative OMP-native routing topology.

RoleBench benchmarks native OMP model roles, but weighted allocation is only
valid for the small set of helper/execution roles that may safely rotate between
independent logical invocations. Default, plan, slow, vision, designer, advisor,
and reviewer retain configured-primary plus ordinary fallback-chain semantics
and are structurally excluded from weighted policies.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator

from .contracts import ContractError, resolve_root


POOL_ROLES: tuple[str, ...] = (
    "smol",
    "commit",
    "tiny",
    "task",
)
FIXED_ROLES: tuple[str, ...] = (
    "default",
    "plan",
    "slow",
    "vision",
    "designer",
    "advisor",
    "reviewer",
)
ROUTING_ROLES: tuple[str, ...] = (*FIXED_ROLES, *POOL_ROLES)

_EXPECTED_STRATEGIES: dict[str, str] = {
    "default": "fallback-chain",
    "plan": "fallback-chain",
    "slow": "fallback-chain",
    "vision": "fallback-chain",
    "designer": "fallback-chain",
    "advisor": "fallback-chain",
    "reviewer": "fallback-chain",
    "smol": "weighted-pool",
    "commit": "weighted-pool",
    "tiny": "weighted-pool",
    "task": "weighted-pool",
}

_TOPOLOGY_FILE = Path("contracts/routing-topology.json")
_TOPOLOGY_SCHEMA = Path("contracts/schemas/routing-topology.schema.json")


@dataclass(frozen=True)
class RoleRouting:
    role: str
    strategy: str
    pool_eligible: bool
    selection_scope: str
    description: str


@dataclass(frozen=True)
class RoutingTopology:
    topology_id: str
    roles: Mapping[str, RoleRouting]

    def strategy_for(self, role: str) -> str:
        try:
            return self.roles[role].strategy
        except KeyError as error:
            raise ContractError(f"unknown routing role {role!r}") from error

    def is_pool_eligible(self, role: str) -> bool:
        return role in self.roles and self.roles[role].pool_eligible


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


def _validate(instance: Mapping[str, object], schema: Mapping[str, object], label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    json_path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    )
    raise ContractError(error.message, label, json_path)


def load_routing_topology(root: Path | None = None) -> RoutingTopology:
    resolved = resolve_root(root)
    document = _load_object(resolved / _TOPOLOGY_FILE)
    schema = _load_object(resolved / _TOPOLOGY_SCHEMA)
    _validate(document, schema, _TOPOLOGY_FILE.as_posix())

    role_values = document.get("roles")
    if not isinstance(role_values, dict):
        raise ContractError("roles must be an object", _TOPOLOGY_FILE.as_posix(), "$.roles")
    if set(role_values) != set(ROUTING_ROLES):
        raise ContractError(
            "routing topology must contain the exact native routing role set",
            _TOPOLOGY_FILE.as_posix(),
            "$.roles",
        )

    roles: dict[str, RoleRouting] = {}
    for role in ROUTING_ROLES:
        value = role_values.get(role)
        if not isinstance(value, dict):
            raise ContractError("role routing entry must be an object", _TOPOLOGY_FILE.as_posix())
        strategy = value.get("strategy")
        pool_eligible = value.get("pool_eligible")
        if strategy != _EXPECTED_STRATEGIES[role]:
            raise ContractError(
                f"role {role!r} must use strategy {_EXPECTED_STRATEGIES[role]!r}",
                _TOPOLOGY_FILE.as_posix(),
                f"$.roles.{role}.strategy",
            )
        expected_pool = role in POOL_ROLES
        if pool_eligible is not expected_pool:
            raise ContractError(
                f"role {role!r} pool_eligible must be {expected_pool}",
                _TOPOLOGY_FILE.as_posix(),
                f"$.roles.{role}.pool_eligible",
            )
        roles[role] = RoleRouting(
            role=role,
            strategy=str(strategy),
            pool_eligible=expected_pool,
            selection_scope=str(value["selection_scope"]),
            description=str(value["description"]),
        )

    topology_id = document.get("topology_id")
    if not isinstance(topology_id, str):
        raise ContractError("topology_id must be a string", _TOPOLOGY_FILE.as_posix())
    return RoutingTopology(topology_id=topology_id, roles=roles)
