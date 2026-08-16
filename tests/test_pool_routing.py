from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from rolebench.contracts import ContractError
from rolebench.pool_routing import (
    load_pool_lane_registry,
    resolve_pool_allocation,
    should_specialize,
    validate_pool_policy,
)
from rolebench.routing_topology import (
    BASELINE_PRIMARY_ROLES,
    BASELINE_WEIGHTED_ROLES,
    ROUTING_ROLES,
    load_routing_topology,
)

ROOT = Path(__file__).resolve().parents[1]
ZERO = "0" * 64


def policy() -> dict[str, object]:
    task_default = {
        "routes": [{"route_id": "provider/a:high", "weight_bps": 10000}],
        "fallback_chain": ["provider/fallback:high"],
    }
    return {
        "schema_version": "omp.pool-policy/v1",
        "policy_id": "test-pool-policy",
        "created_at": "2026-08-16T00:00:00Z",
        "valid_from": "2026-08-16T00:00:00Z",
        "valid_until": "2026-08-17T00:00:00Z",
        "routing_topology": {
            "schema_version": "omp.routing-topology/v1",
            "topology_id": "omp-native-routing-topology-v1",
            "checksum_sha256": ZERO,
        },
        "lane_registry": {
            "schema_version": "omp.pool-lane-registry/v1",
            "registry_id": "omp-task-pool-lanes-v1",
            "checksum_sha256": ZERO,
        },
        "evidence_snapshot": {"schema_version":"omp.pool-evidence/v1","snapshot_id":"evidence-test","checksum_sha256":ZERO},
        "capacity_snapshot": {"schema_version":"omp.capacity-snapshot/v1","snapshot_id":"capacity-test","checksum_sha256":ZERO},
        "fixed_load_snapshot": {"schema_version":"omp.fixed-load-snapshot/v1","snapshot_id":"fixed-load-test","checksum_sha256":ZERO},
        "optimizer_version": "test",
        "allocation_algorithm": "weighted-rendezvous-v1",
        "allocation_seed": "seed",
        "pools": {
            "task": {
                "quality_floor": 0.8,
                "reliability_floor": 0.95,
                "confidence": 0.95,
                "stickiness_scope": "child-session",
                "default": task_default,
                "lanes": {
                    "debugging": {
                        "routes": [
                            {"route_id": "provider/b:high", "weight_bps": 6000},
                            {"route_id": "provider/a:high", "weight_bps": 4000},
                        ],
                        "fallback_chain": ["provider/fallback:high"],
                        "evidence": {
                            "status": "specialized",
                            "direct_sample_count": 12,
                            "estimated_regret_reduction": 0.02,
                            "inheritance_source": "lane",
                        },
                    }
                },
            }
        },
        "checksum_sha256": ZERO,
    }


class NativePoolRoutingTests(unittest.TestCase):
    def test_every_native_role_supports_primary_and_weighted(self) -> None:
        topology = load_routing_topology(ROOT)
        self.assertEqual(set(topology.roles), set(ROUTING_ROLES))
        for role in ROUTING_ROLES:
            with self.subTest(role=role):
                self.assertTrue(topology.supports_strategy(role, "primary"))
                self.assertTrue(topology.supports_strategy(role, "weighted"))

    def test_baseline_policy_is_not_capability_boundary(self) -> None:
        topology = load_routing_topology(ROOT)
        self.assertEqual(BASELINE_WEIGHTED_ROLES, ("smol", "commit", "tiny", "task"))
        self.assertEqual(set(BASELINE_PRIMARY_ROLES), set(ROUTING_ROLES) - set(BASELINE_WEIGHTED_ROLES))
        for role in BASELINE_WEIGHTED_ROLES:
            self.assertEqual(topology.baseline_strategy_for(role), "weighted")
        for role in BASELINE_PRIMARY_ROLES:
            self.assertEqual(topology.baseline_strategy_for(role), "primary")

    def test_registry_contains_only_task_lanes(self) -> None:
        registry = load_pool_lane_registry(ROOT)
        self.assertEqual(len(registry.lanes), 6)
        self.assertTrue(all(lane.role == "task" for lane in registry.lanes.values()))

    def test_valid_task_lane_policy(self) -> None:
        validate_pool_policy(policy(), root=ROOT)

    def test_any_native_role_can_be_weighted(self) -> None:
        for role in ROUTING_ROLES:
            with self.subTest(role=role):
                candidate = deepcopy(policy())
                if role == "task":
                    validate_pool_policy(candidate, root=ROOT)
                    continue
                candidate["pools"] = {
                    role: {
                        "quality_floor": 0.8,
                        "reliability_floor": 0.95,
                        "confidence": 0.95,
                        "stickiness_scope": load_routing_topology(ROOT).roles[role].selection_scope,
                        "default": {
                            "routes": [{"route_id":"provider/a:high","weight_bps":10000}],
                            "fallback_chain": ["provider/fallback:high"],
                        },
                    }
                }
                validate_pool_policy(candidate, root=ROOT)

    def test_weighted_pool_requires_fallback_chain(self) -> None:
        candidate = deepcopy(policy())
        del candidate["pools"]["task"]["default"]["fallback_chain"]
        with self.assertRaises(ContractError):
            validate_pool_policy(candidate, root=ROOT)

    def test_unknown_task_lane_is_rejected(self) -> None:
        candidate = deepcopy(policy())
        lanes = candidate["pools"]["task"]["lanes"]
        lanes["mystery"] = lanes.pop("debugging")
        with self.assertRaises(ContractError):
            validate_pool_policy(candidate, root=ROOT)

    def test_non_specialized_lane_must_inherit_default(self) -> None:
        candidate = deepcopy(policy())
        lane = candidate["pools"]["task"]["lanes"]["debugging"]
        lane["evidence"] = {
            "status": "insufficient-evidence",
            "direct_sample_count": 2,
            "estimated_regret_reduction": 0.0,
            "inheritance_source": "role-default",
        }
        with self.assertRaises(ContractError):
            validate_pool_policy(candidate, root=ROOT)

    def test_specialization_requires_evidence_and_regret_reduction(self) -> None:
        lane = load_pool_lane_registry(ROOT).lanes["task/debugging"]
        self.assertFalse(should_specialize(lane, direct_sample_count=11, estimated_regret_reduction=0.10))
        self.assertFalse(should_specialize(lane, direct_sample_count=12, estimated_regret_reduction=0.009))
        self.assertTrue(should_specialize(lane, direct_sample_count=12, estimated_regret_reduction=0.01))

    def test_resolution_falls_back_to_task_default(self) -> None:
        candidate = policy()
        default = candidate["pools"]["task"]["default"]
        self.assertIs(resolve_pool_allocation(candidate, role="task"), default)
        self.assertIs(
            resolve_pool_allocation(candidate, role="task", lane="unknown", registry=load_pool_lane_registry(ROOT)),
            default,
        )
        specialized = resolve_pool_allocation(candidate, role="task", lane="debugging")
        self.assertEqual(specialized["routes"][0]["route_id"], "provider/b:high")


if __name__ == "__main__":
    unittest.main()
