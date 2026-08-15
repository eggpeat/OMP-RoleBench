from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rolebench.accounting import AccountingError, classify_attempt
from rolebench.accounting_rules import ATTEMPT_OUTCOME_RULES
from rolebench.contracts import JSONObject, canonical_json
from rolebench.fault_harness import FaultHarnessError, _observation, run_fault_check


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = Path("contracts/scored-worker-policy.json")


class FaultHarnessTests(unittest.TestCase):
    def run_report(self) -> dict[str, object]:
        return run_fault_check(PRODUCT_ROOT, POLICY_PATH)

    def test_exact_reason_coverage_and_accounting_totals(self) -> None:
        report = self.run_report()

        self.assertTrue(report["passed"])
        self.assertEqual(
            report["schema_version"],
            "omp.worker-fault-check-report/v1",
        )
        self.assertEqual(report["external_calls"], 0)
        self.assertEqual(report["scenario_count"], 29)
        self.assertEqual(report["expected_reason_count"], 29)
        self.assertEqual(report["covered_reason_count"], 29)
        self.assertEqual(report["passed_scenarios"], 29)
        self.assertEqual(report["failed_scenarios"], 0)
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(len(report["covered_reason_codes"]), 29)
        self.assertEqual(
            report["quality_summary"],
            {
                "total_attempts": 29,
                "scored_attempts": 4,
                "accepted": 1,
                "rejected": 3,
                "quality_score": 0.25,
                "not_scored": {
                    "retryable_invalid": 18,
                    "quarantined": 5,
                    "cancelled": 1,
                    "excluded": 1,
                },
            },
        )
        scenarios = report["scenarios"]
        self.assertIsInstance(scenarios, list)
        self.assertTrue(all(item["passed"] for item in scenarios))
        self.assertTrue(all(item["observation_valid"] for item in scenarios))
        self.assertTrue(all(item["outcome_valid"] for item in scenarios))
        self.assertTrue(all(item["diagnostics"] == [] for item in scenarios))

    def test_aggregate_quality_divergence_fails_gate(self) -> None:
        wrong_summary = {
            "total_attempts": 29,
            "scored_attempts": 4,
            "accepted": 2,
            "rejected": 2,
            "quality_score": 0.5,
            "not_scored": {
                "retryable_invalid": 18,
                "quarantined": 5,
                "cancelled": 1,
                "excluded": 1,
            },
        }
        with patch(
            "rolebench.fault_harness.summarize_outcomes",
            return_value=wrong_summary,
        ):
            report = self.run_report()

        self.assertFalse(report["passed"])
        self.assertEqual(report["failed_scenarios"], 0)
        self.assertEqual(report["quality_summary"], wrong_summary)
        self.assertEqual(
            report["diagnostics"],
            [
                "quality summary mismatch: "
                'expected {"accepted":1,"not_scored":{"cancelled":1,"excluded":1,'
                '"quarantined":5,"retryable_invalid":18},"quality_score":0.25,'
                '"rejected":3,"scored_attempts":4,"total_attempts":29}, '
                'actual {"accepted":2,"not_scored":{"cancelled":1,"excluded":1,'
                '"quarantined":5,"retryable_invalid":18},"quality_score":0.5,'
                '"rejected":2,"scored_attempts":4,"total_attempts":29}'
            ],
        )

    def test_invalid_classification_fails_closed_without_crashing(self) -> None:
        def invalid_classification(observation: JSONObject) -> JSONObject:
            outcome = classify_attempt(observation)
            outcome["disposition"] = "invalid-for-test"
            return outcome

        with patch(
            "rolebench.fault_harness.classify_attempt",
            side_effect=invalid_classification,
        ):
            report = self.run_report()

        self.assertFalse(report["passed"])
        self.assertEqual(report["failed_scenarios"], 29)
        self.assertEqual(report["quality_summary"]["total_attempts"], 0)
        scenarios = report["scenarios"]
        self.assertIsInstance(scenarios, list)
        self.assertTrue(all(not item["outcome_valid"] for item in scenarios))
        self.assertTrue(
            all(
                any("invalid-for-test" in diagnostic for diagnostic in item["diagnostics"])
                for item in scenarios
            )
        )

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
        excluded = actual_by_reason["non-scored-evidence"]
        self.assertEqual(excluded["disposition"], "excluded")
        self.assertFalse(excluded["counts_toward_quality"])

    def test_classification_error_returns_structured_failed_report(self) -> None:
        with patch(
            "rolebench.fault_harness.classify_attempt",
            side_effect=AccountingError("forced classification failure"),
        ):
            report = self.run_report()

        self.assertFalse(report["passed"])
        self.assertEqual(report["failed_scenarios"], 29)
        self.assertEqual(
            report["quality_summary"],
            {
                "total_attempts": 0,
                "scored_attempts": 0,
                "accepted": 0,
                "rejected": 0,
                "quality_score": None,
                "not_scored": {
                    "retryable_invalid": 0,
                    "quarantined": 0,
                    "cancelled": 0,
                    "excluded": 0,
                },
            },
        )
        self.assertTrue(
            any(
                "quality summary mismatch" in str(diagnostic)
                for diagnostic in report["diagnostics"]
            )
        )
        scenarios = report["scenarios"]
        self.assertIsInstance(scenarios, list)
        for scenario in scenarios:
            self.assertFalse(scenario["passed"])
            self.assertFalse(scenario["outcome_valid"])
            self.assertIn(
                "classification error: forced classification failure",
                scenario["diagnostics"],
            )

    def test_reason_code_coverage_divergence_fails_gate(self) -> None:
        added_rule = ATTEMPT_OUTCOME_RULES["verifier-accepted"]
        variants = (
            (
                {**ATTEMPT_OUTCOME_RULES, "future-reason": added_rule},
                ["future-reason"],
                [],
            ),
            (
                {
                    reason: rule
                    for reason, rule in ATTEMPT_OUTCOME_RULES.items()
                    if reason != "verifier-accepted"
                },
                [],
                ["verifier-accepted"],
            ),
        )
        for rules, missing, unexpected in variants:
            with self.subTest(missing=missing, unexpected=unexpected):
                with patch(
                    "rolebench.fault_harness.ATTEMPT_OUTCOME_RULES",
                    rules,
                ):
                    report = self.run_report()

                self.assertFalse(report["passed"])
                self.assertEqual(report["failed_scenarios"], 0)
                self.assertEqual(report["passed_scenarios"], 29)
                self.assertEqual(report["missing_reason_codes"], missing)
                self.assertEqual(report["unexpected_reason_codes"], unexpected)

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
