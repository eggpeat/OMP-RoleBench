"""Authoritative OMP-native routing topology.

Every native OMP model role supports either configured-primary selection or
weighted selection. Retry fallback chains are orthogonal recovery semantics and
remain available under either selection strategy. The topology defines stable
selection scopes and lane support; `baseline_strategy` records RoleBench's
recommended/default policy, not an upstream capability restriction.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator

from .contracts import ContractError, resolve_root


ROUTING_ROLES: tuple[str, ...] = (
    "default",
    "plan",
    "advisor",
    "reviewer",
    "smol",
    "slow",
    "vision",
    "designer",
    "commit",
    "tiny",
    "task",
)
BASELINE_WEIGHTED_ROLES: tuple[str, ...] = ("smol", "commit", "tiny", "task")
BASELINE_PRIMARY_ROLES: tuple[str, ...] = tuple(role for role in ROUTING_ROLES if role not in BASELINE_WEIGHTED_ROLES)
SUPPORTED_STRATEGIES: tuple[str, ...] = ("primary", "weighted")

_EXPECTED_SCOPES: dict[str, str] = {
    "default": "main-session",
    "plan": "role-session",
    "advisor": "advisor-runtime",
    "reviewer": "review-run",
    "smol": "operation",
    "slow": "role-session",
    "vision": "operation",
    "designer": "child-session",
    "commit": "operation",
    "tiny": "operation",
    "task": "child-session",
}

_TOPOLOGY_FILE = Path("contracts/routing-topology.json")
_TOPOLOGY_SCHEMA = Path("contracts/schemas/routing-topology.schema.json")


@dataclass(frozen=True)
class RoleRouting:
    role: str
    selection_scope: str
    supported_strategies: tuple[str, ...]
    baseline_strategy: str
    supports_lanes: bool
    description: str


@dataclass(frozen=True)
class RoutingTopology:
    topology_id: str
    roles: Mapping[str, RoleRouting]

    def supports_strategy(self, role: str, strategy: str) -> bool:
        try:
            return strategy in self.roles[role].supported_strategies
        except KeyError as error:
            raise ContractError(f"unknown routing role {role!r}") from error

    def baseline_strategy_for(self, role: str) -> str:
        try:
            return self.roles[role].baseline_strategy
        except KeyError as error:
            raise ContractError(f"unknown routing role {role!r}") from error


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
        supported = value.get("supported_strategies")
        if supported != list(SUPPORTED_STRATEGIES):
            raise ContractError(
                f"role {role!r} must support both primary and weighted selection",
                _TOPOLOGY_FILE.as_posix(),
                f"$.roles.{role}.supported_strategies",
            )
        if value.get("selection_scope") != _EXPECTED_SCOPES[role]:
            raise ContractError(
                f"role {role!r} must use selection scope {_EXPECTED_SCOPES[role]!r}",
                _TOPOLOGY_FILE.as_posix(),
                f"$.roles.{role}.selection_scope",
            )
        expected_baseline = "weighted" if role in BASELINE_WEIGHTED_ROLES else "primary"
        if value.get("baseline_strategy") != expected_baseline:
            raise ContractError(
                f"role {role!r} baseline_strategy must be {expected_baseline!r}",
                _TOPOLOGY_FILE.as_posix(),
                f"$.roles.{role}.baseline_strategy",
            )
        expected_lanes = role == "task"
        if value.get("supports_lanes") is not expected_lanes:
            raise ContractError(
                f"role {role!r} supports_lanes must be {expected_lanes}",
                _TOPOLOGY_FILE.as_posix(),
                f"$.roles.{role}.supports_lanes",
            )
        roles[role] = RoleRouting(
            role=role,
            selection_scope=str(value["selection_scope"]),
            supported_strategies=SUPPORTED_STRATEGIES,
            baseline_strategy=expected_baseline,
            supports_lanes=expected_lanes,
            description=str(value["description"]),
        )

    topology_id = document.get("topology_id")
    if not isinstance(topology_id, str):
        raise ContractError("topology_id must be a string", _TOPOLOGY_FILE.as_posix())
    return RoutingTopology(topology_id=topology_id, roles=roles)
