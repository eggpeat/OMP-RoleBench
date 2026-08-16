"""Tests for task routing lane binding and validation."""

from __future__ import annotations

from pathlib import Path
import unittest

from rolebench.contracts import (
    BUILTIN_ROLES,
    discover_root,
    validate_value,
)


class TestTaskLaneBinding(unittest.TestCase):
    def setUp(self) -> None:
        self.root = discover_root(Path(__file__).parent)

    def test_builtin_roles_contains_reviewer(self) -> None:
        self.assertIn("reviewer", BUILTIN_ROLES)
        self.assertEqual(len(BUILTIN_ROLES), 11)

    def test_task_schema_allows_valid_routing_lane(self) -> None:
        valid_task = {
            "schema_version": "omp.diagnostic-task/v2",
            "task_id": "test.task-lane",
            "task_version": "1.0.0",
            "content_digest_sha256": "0" * 64,
            "routing_eligible": False,
            "family": {
                "family_id": "test.task-lane",
                "source": "authored",
                "disjoint_key": "test.task-lane",
            },
            "split": {
                "partition": "anchor",
                "family_id": "test.task-lane",
                "assignment_method": "author-assigned",
                "confidentiality": "public",
            },
            "authorship": {"author": "rolebench/test"},
            "reviews": {
                "license": {
                    "decision": "approved",
                    "reviewer": "rolebench/reviewer",
                    "reviewed_at": "2026-08-16T00:00:00Z",
                    "evidence_path": "reviews/license.json",
                    "evidence_digest_sha256": "0" * 64,
                },
                "privacy": {
                    "decision": "approved",
                    "reviewer": "rolebench/reviewer",
                    "reviewed_at": "2026-08-16T00:00:00Z",
                    "evidence_path": "reviews/privacy.json",
                    "evidence_digest_sha256": "0" * 64,
                },
                "verifier": {
                    "decision": "approved",
                    "reviewer": "rolebench/reviewer",
                    "reviewed_at": "2026-08-16T00:00:00Z",
                    "evidence_path": "reviews/verifier.json",
                    "evidence_digest_sha256": "0" * 64,
                },
                "split": {
                    "decision": "approved",
                    "reviewer": "rolebench/reviewer",
                    "reviewed_at": "2026-08-16T00:00:00Z",
                    "evidence_path": "reviews/split.json",
                    "evidence_digest_sha256": "0" * 64,
                },
            },
            "role": "task",
            "routing_lane": "task/implementation",
            "role_contract": {
                "contract_id": "role-contract/task/v1",
                "digest_sha256": "0" * 64,
            },
            "task_mix": "task-v1",
            "capability_tags": ["code-editing"],
            "difficulty": "medium",
            "partition": "anchor",
            "source": {
                "kind": "authored",
                "version": "1.0.0",
                "digest_sha256": "0" * 64,
                "task": "test.task-lane",
            },
            "license": {"expression": "MIT", "redistribution": "permitted"},
            "assets": {
                "prompt": {"path": "public/prompt.txt", "kind": "file", "digest_sha256": "0" * 64},
                "workspace": {"path": "public/workspace", "kind": "tree", "digest_sha256": "0" * 64},
                "verifier_private": {"path": "verifier-private", "kind": "tree", "digest_sha256": "0" * 64},
            },
            "policy": {"path": "contracts/scored-worker-policy-v2.json", "digest_sha256": "0" * 64},
            "agent": {
                "image": "rolebench.local/agent@sha256:" + "0" * 64,
                "config_digest_sha256": "0" * 64,
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": "0" * 64,
                "argv": ["run"],
            },
            "admission_agents": {
                "baseline": {
                    "image": "rolebench.local/agent@sha256:" + "0" * 64,
                    "config_digest_sha256": "0" * 64,
                    "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                    "asset_tree_digest_sha256": "0" * 64,
                    "argv": ["run"],
                },
                "reference": {
                    "image": "rolebench.local/agent@sha256:" + "0" * 64,
                    "config_digest_sha256": "0" * 64,
                    "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                    "asset_tree_digest_sha256": "0" * 64,
                    "argv": ["run"],
                },
                "tamper": {
                    "image": "rolebench.local/agent@sha256:" + "0" * 64,
                    "config_digest_sha256": "0" * 64,
                    "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                    "asset_tree_digest_sha256": "0" * 64,
                    "argv": ["run"],
                },
            },
            "runner": {
                "image": "rolebench.local/runner@sha256:" + "0" * 64,
                "config_digest_sha256": "0" * 64,
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": "0" * 64,
                "argv": ["run"],
            },
            "verifier": {
                "image": "rolebench.local/verifier@sha256:" + "0" * 64,
                "config_digest_sha256": "0" * 64,
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": "0" * 64,
                "argv": ["run"],
            },
            "objective": {
                "kind": "executable-test",
                "criteria": ["pass tests"],
                "verifier_type": "executable-task-verifier",
            },
            "unscored_failures": ["infrastructure", "provider", "runner", "verifier"],
        }
        result = validate_value(self.root, "diagnostic-task", valid_task, Path("test.json"))
        # Check that schema allows valid lane
        schema_diags = [d for d in result.diagnostics if "routing_lane" in d.message or "routing_lane" in d.json_path]
        self.assertEqual(schema_diags, [])

        # Non-task role cannot have routing_lane
        non_task = dict(valid_task)
        non_task["role"] = "commit"
        result_non_task = validate_value(self.root, "diagnostic-task", non_task, Path("test.json"))
        self.assertTrue(any("routing_lane is not permitted for non-task role" in d.message for d in result_non_task.diagnostics))

        # Invalid lane rejected
        invalid_lane_task = dict(valid_task)
        invalid_lane_task["routing_lane"] = "task/nonexistent"
        result_invalid = validate_value(self.root, "diagnostic-task", invalid_lane_task, Path("test.json"))
        self.assertTrue(any("routing_lane" in d.message or "routing_lane" in d.json_path for d in result_invalid.diagnostics))

    def test_task_pack_schema_allows_routing_lane_on_entries(self) -> None:
        valid_pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "task-pack/task/v1",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {
                "contract_id": "role-contract/task/v1",
                "digest_sha256": "0" * 64,
            },
            "partition": "anchor",
            "status": "calibration",
            "routing_eligible": False,
            "selection_method": "fixed-anchor",
            "required_capabilities": ["autonomous-task-execution"],
            "entries": [
                {
                    "task": {"path": "contracts/tasks/terminal-bench.cancel-async-tasks/2.1-r6/task.json", "digest_sha256": "0" * 64},
                    "qualification": {"path": "contracts/tasks/terminal-bench.cancel-async-tasks/2.1-r6/qualification.json", "digest_sha256": "0" * 64},
                    "routing_lane": "task/debugging",
                }
            ],
        }
        result = validate_value(self.root, "task-pack", valid_pack, Path("contracts/task-packs/task-v1.json"))
        lane_diags = [d for d in result.diagnostics if "routing_lane" in d.message or "routing_lane" in d.json_path]
        self.assertEqual(lane_diags, [])


if __name__ == "__main__":
    unittest.main()
