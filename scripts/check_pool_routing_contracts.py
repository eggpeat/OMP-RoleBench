#!/usr/bin/env python3
"""Validate the OMP-native routing topology and task pool-lane registry."""

from __future__ import annotations

import json
from pathlib import Path

from rolebench.pool_routing import load_pool_lane_registry
from rolebench.routing_topology import (
    BASELINE_PRIMARY_ROLES,
    BASELINE_WEIGHTED_ROLES,
    ROUTING_ROLES,
    SUPPORTED_STRATEGIES,
    load_routing_topology,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    topology = load_routing_topology(root)
    lanes = load_pool_lane_registry(root)
    print(
        json.dumps(
            {
                "valid": True,
                "topology_id": topology.topology_id,
                "roles": list(ROUTING_ROLES),
                "supported_strategies": list(SUPPORTED_STRATEGIES),
                "baseline_primary_roles": list(BASELINE_PRIMARY_ROLES),
                "baseline_weighted_roles": list(BASELINE_WEIGHTED_ROLES),
                "selection_scopes": {
                    role: topology.roles[role].selection_scope for role in ROUTING_ROLES
                },
                "task_lanes": sorted(lanes.lanes),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
