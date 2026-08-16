#!/usr/bin/env python3
"""Validate the OMP-native routing topology and task pool-lane registry."""

from __future__ import annotations

import json
from pathlib import Path

from rolebench.pool_routing import load_pool_lane_registry
from rolebench.routing_topology import FIXED_ROLES, POOL_ROLES, load_routing_topology


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    topology = load_routing_topology(root)
    lanes = load_pool_lane_registry(root)
    print(
        json.dumps(
            {
                "valid": True,
                "topology_id": topology.topology_id,
                "fallback_chain_roles": [
                    role for role in FIXED_ROLES if topology.strategy_for(role) == "fallback-chain"
                ],
                "dedicated_roles": [
                    role for role in FIXED_ROLES if topology.strategy_for(role) == "dedicated"
                ],
                "manual_roles": [
                    role for role in FIXED_ROLES if topology.strategy_for(role) == "manual"
                ],
                "pool_roles": list(POOL_ROLES),
                "task_lanes": sorted(lanes.lanes),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
