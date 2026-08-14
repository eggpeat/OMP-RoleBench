from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from rolebench.contracts import (
    canonical_digest,
    load_repository,
    validate_artifact,
    validate_repository,
)


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = PRODUCT_ROOT / "contracts"
POLICY = "contracts/scored-worker-policy.json"
SCHEMA = "contracts/schemas/scored-worker-policy.schema.json"


class WorkerPolicyFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(CONTRACTS_ROOT, self.root / "contracts")

    def read_json(self, relative: str) -> dict[str, object]:
        value = json.loads((self.root / relative).read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def write_json(self, relative: str, value: dict[str, object]) -> None:
        (self.root / relative).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def diagnostics(self) -> set[tuple[str, str, str]]:
        return {
            (diagnostic.file, diagnostic.json_path, diagnostic.message)
            for diagnostic in validate_repository(self.root).diagnostics
        }

    def policy(self) -> dict[str, object]:
        return self.read_json(POLICY)


class ValidWorkerPolicyTests(WorkerPolicyFixture):
    def test_canonical_policy_and_repository_are_valid(self) -> None:
        repository_result = validate_repository(self.root)
        self.assertTrue(repository_result.valid, repository_result.diagnostics)

        artifact_result = validate_artifact(
            self.root,
            "scored-worker-policy",
            Path(POLICY),
        )
        self.assertTrue(artifact_result.valid, artifact_result.diagnostics)
        self.assertEqual(
            load_repository(self.root).scored_worker_policy,
            self.policy(),
        )

    def test_schema_encodes_fail_closed_sandbox_and_accounting_constants(self) -> None:
        schema = self.read_json(SCHEMA)
        definitions = schema["$defs"]
        self.assertIsInstance(definitions, dict)

        executor = definitions["executor"]
        network = definitions["network"]
        verifier = definitions["verifier"]
        accounting = definitions["accounting"]
        resources = definitions["resources"]
        timeouts = definitions["timeouts"]
        handoff = definitions["handoff"]
        for value in (executor, network, verifier, accounting, resources, timeouts, handoff):
            self.assertIsInstance(value, dict)
            self.assertFalse(value["additionalProperties"])

        self.assertEqual(executor["properties"]["privileged"], {"const": False})
        self.assertEqual(executor["properties"]["rootless"], {"const": True})
        self.assertEqual(executor["properties"]["devices"], {"const": []})
        self.assertEqual(executor["properties"]["mounts"], {"const": []})
        self.assertEqual(network["properties"]["direct_egress"], {"const": False})
        self.assertEqual(network["properties"]["raw_provider_credentials"], {"const": False})
        self.assertEqual(verifier["properties"]["network"], {"const": "none"})
        self.assertEqual(accounting["properties"]["fail_closed"], {"const": True})
        self.assertEqual(
            accounting["properties"]["quality_denominator"],
            {"const": "valid-scored-attempts-only"},
        )
        self.assertEqual(handoff["properties"]["require_runner_exit"], {"const": True})
        self.assertEqual(handoff["properties"]["runner_input_read_only"], {"const": True})
        self.assertEqual(handoff["properties"]["verifier_input_read_only"], {"const": True})
        self.assertNotIn("verifier_read_only", handoff["properties"])

        for definition_name in ("resources", "timeouts"):
            properties = definitions[definition_name]["properties"]
            for field, constraint in properties.items():
                with self.subTest(definition=definition_name, field=field):
                    self.assertIn("maximum", constraint)
                    self.assertTrue(
                        constraint.get("minimum") == 1
                        or constraint.get("exclusiveMinimum") == 0
                    )

    def test_policy_changes_contribute_to_canonical_repository_digest(self) -> None:
        original = canonical_digest(load_repository(self.root))
        policy = self.policy()
        policy["policy_id"] = "rolebench-scored-worker-v1-modified"
        self.write_json(POLICY, policy)
        changed = canonical_digest(load_repository(self.root))
        self.assertNotEqual(original, changed)


class InvalidWorkerPolicyTests(WorkerPolicyFixture):
    def test_missing_policy_and_schema_fail_closed_with_required_diagnostics(self) -> None:
        (self.root / POLICY).unlink()
        (self.root / SCHEMA).unlink()
        diagnostics = self.diagnostics()
        self.assertIn(
            (
                SCHEMA,
                "$",
                "required schema file is missing or invalid",
            ),
            diagnostics,
        )
        self.assertIn(
            (
                POLICY,
                "$",
                "required canonical policy file is missing or invalid",
            ),
            diagnostics,
        )

    def test_unsafe_constant_is_rejected_at_exact_path(self) -> None:
        policy = self.policy()
        executor = policy["executor"]
        self.assertIsInstance(executor, dict)
        executor["privileged"] = True
        self.write_json(POLICY, policy)
        self.assertIn(
            (POLICY, "$.executor.privileged", "False was expected"),
            self.diagnostics(),
        )

    def test_worker_and_verifier_ids_must_be_distinct(self) -> None:
        policy = self.policy()
        executor = policy["executor"]
        verifier = policy["verifier"]
        self.assertIsInstance(executor, dict)
        self.assertIsInstance(verifier, dict)
        executor_user = executor["user"]
        verifier_user = verifier["user"]
        self.assertIsInstance(executor_user, dict)
        self.assertIsInstance(verifier_user, dict)
        verifier_user["uid"] = executor_user["uid"]
        verifier_user["gid"] = executor_user["gid"]
        self.write_json(POLICY, policy)
        diagnostics = self.diagnostics()
        self.assertIn(
            (POLICY, "$.verifier.user.uid", "must differ from executor user uid"),
            diagnostics,
        )
        self.assertIn(
            (POLICY, "$.verifier.user.gid", "must differ from executor user gid"),
            diagnostics,
        )

    def test_total_timeout_must_cover_every_phase_and_grace(self) -> None:
        policy = self.policy()
        timeouts = policy["timeouts"]
        self.assertIsInstance(timeouts, dict)
        timeouts["total_seconds"] = 5000
        self.write_json(POLICY, policy)
        self.assertIn(
            (
                POLICY,
                "$.timeouts.total_seconds",
                "must be at least the sum of setup, agent, artifact, runner, verifier, and termination grace timeouts",
            ),
            self.diagnostics(),
        )

    def test_termination_grace_must_be_shorter_than_every_other_phase(self) -> None:
        policy = self.policy()
        timeouts = policy["timeouts"]
        self.assertIsInstance(timeouts, dict)
        timeouts["termination_grace_seconds"] = timeouts["setup_seconds"]
        timeouts["artifact_seconds"] = 301
        timeouts["total_seconds"] = 7000
        self.write_json(POLICY, policy)
        matching = {
            diagnostic
            for diagnostic in self.diagnostics()
            if diagnostic[1] == "$.timeouts.termination_grace_seconds"
        }
        self.assertEqual(
            matching,
            {
                (
                    POLICY,
                    "$.timeouts.termination_grace_seconds",
                    "must be shorter than setup_seconds",
                )
            },
        )

    def test_artifact_limit_must_fit_executor_scratch(self) -> None:
        policy = self.policy()
        resources = policy["resources"]
        executor = policy["executor"]
        self.assertIsInstance(resources, dict)
        self.assertIsInstance(executor, dict)
        scratch = executor["scratch"]
        self.assertIsInstance(scratch, dict)
        resources["artifact_bytes_limit"] = 4096
        scratch["size_bytes"] = 2048
        self.write_json(POLICY, policy)
        self.assertIn(
            (
                POLICY,
                "$.resources.artifact_bytes_limit",
                "must not exceed executor scratch size_bytes",
            ),
            self.diagnostics(),
        )

    def test_oversized_integers_fail_closed_without_crashing(self) -> None:
        policy = self.policy()
        resources = policy["resources"]
        timeouts = policy["timeouts"]
        self.assertIsInstance(resources, dict)
        self.assertIsInstance(timeouts, dict)
        resources["memory_bytes"] = 10**400
        timeouts["setup_seconds"] = 1.5
        timeouts["agent_seconds"] = 10**400
        timeouts["total_seconds"] = 10**400
        self.write_json(POLICY, policy)

        result = validate_artifact(
            self.root,
            "scored-worker-policy",
            Path(POLICY),
        )
        self.assertFalse(result.valid)
        paths = {diagnostic.json_path for diagnostic in result.diagnostics}
        self.assertIn("$.resources.memory_bytes", paths)
        self.assertIn("$.timeouts.agent_seconds", paths)
        self.assertIn("$.timeouts.total_seconds", paths)

    def test_invalid_policy_id_reports_exact_schema_diagnostic(self) -> None:
        policy = copy.deepcopy(self.policy())
        policy["policy_id"] = ""
        self.write_json(POLICY, policy)
        self.assertIn(
            (POLICY, "$.policy_id", "'' is too short"),
            self.diagnostics(),
        )


if __name__ == "__main__":
    unittest.main()
