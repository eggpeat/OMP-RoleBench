from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from rolebench.contracts import canonical_json
from rolebench.fault_harness import FaultHarnessError, _observation, run_fault_check


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = Path("contracts/scored-worker-policy.json")


class FaultHarnessTests(unittest.TestCase):
    def run_report(self) -> dict[str, object]:
        return run_fault_check(PRODUCT_ROOT, POLICY_PATH)

    def test_exact_reason_coverage_and_accounting_totals(self) -> None:
        report = self.run_report()

        self.assertTrue(report["passed"])
        self.assertEqual(report["external_calls"], 0)
        self.assertEqual(report["scenario_count"], 28)
        self.assertEqual(report["expected_reason_count"], 28)
        self.assertEqual(report["covered_reason_count"], 28)
        self.assertEqual(report["passed_scenarios"], 28)
        self.assertEqual(report["failed_scenarios"], 0)
        self.assertEqual(len(report["covered_reason_codes"]), 28)
        self.assertEqual(
            report["quality_summary"],
            {
                "total_attempts": 28,
                "scored_attempts": 4,
                "accepted": 1,
                "rejected": 3,
                "quality_score": 0.25,
                "not_scored": {
                    "retryable_invalid": 18,
                    "quarantined": 5,
                    "cancelled": 1,
                },
            },
        )
        scenarios = report["scenarios"]
        self.assertIsInstance(scenarios, list)
        self.assertTrue(all(item["passed"] for item in scenarios))
        self.assertTrue(all(item["observation_valid"] for item in scenarios))
        self.assertTrue(all(item["outcome_valid"] for item in scenarios))
        self.assertTrue(all(item["diagnostics"] == [] for item in scenarios))

    def test_report_is_deterministic(self) -> None:
        first = self.run_report()
        second = self.run_report()

        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_policy_digest_is_canonical_and_propagated_to_observations(self) -> None:
        policy = json.loads((PRODUCT_ROOT / POLICY_PATH).read_text(encoding="utf-8"))
        digest = sha256(canonical_json(policy).encode("utf-8")).hexdigest()

        report = self.run_report()
        self.assertEqual(report["policy_digest_sha256"], digest)
        scenarios = report["scenarios"]
        self.assertIsInstance(scenarios, list)
        for scenario in scenarios:
            observation = _observation(scenario["scenario"], digest)
            self.assertEqual(observation["digests"]["runtime_policy"], digest)

    def test_all_infrastructure_and_verifier_faults_are_unscored(self) -> None:
        unscored_reasons = {
            "environment-startup",
            "image-pull",
            "broken-entrypoint",
            "dependency-setup",
            "runner-failure",
            "artifact-collection",
            "provider-rate-limit",
            "provider-server-error",
            "provider-auth-error",
            "provider-network-error",
            "runtime-incompatible",
            "host-resource-exhaustion",
            "orchestrator-timeout",
            "verifier-crash",
            "verifier-result-missing",
            "verifier-result-malformed",
            "verifier-unhealthy",
            "incomplete-observation",
        }
        report = self.run_report()
        scenarios = report["scenarios"]
        self.assertIsInstance(scenarios, list)
        actual_by_reason = {item["scenario"]: item["actual"] for item in scenarios}

        self.assertEqual(set(actual_by_reason) & unscored_reasons, unscored_reasons)
        for reason in unscored_reasons:
            actual = actual_by_reason[reason]
            self.assertEqual(actual["disposition"], "retryable-invalid")
            self.assertFalse(actual["counts_toward_quality"])

    def test_invalid_or_tampered_policy_fails_closed(self) -> None:
        policy = json.loads((PRODUCT_ROOT / POLICY_PATH).read_text(encoding="utf-8"))
        invalid = deepcopy(policy)
        invalid["executor"]["rootless"] = False

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tampered-policy.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(FaultHarnessError, "invalid scored-worker policy"):
                run_fault_check(PRODUCT_ROOT, path)


if __name__ == "__main__":
    unittest.main()
