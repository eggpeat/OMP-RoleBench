from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rolebench.cli import build_parser, run
from rolebench.contracts import JSONObject, canonical_json
from rolebench.fault_harness import FaultHarnessError


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PRODUCT_ROOT / "contracts" / "scored-worker-policy.json"
SHA = "a" * 64


def fault_report(*, passed: bool = True) -> JSONObject:
    expected = {
        "reason_code": "verifier-accepted",
        "disposition": "scored",
        "failure_domain": "none",
        "model_outcome": "accepted",
        "verifier_outcome": "accepted",
        "counts_toward_quality": True,
    }
    scenario = {
        "scenario": "verifier-accepted",
        "expected": expected,
        "actual": deepcopy(expected),
        "observation_valid": True,
        "outcome_valid": True,
        "passed": passed,
        "diagnostics": [] if passed else ["unexpected outcome"],
    }
    return {
        "schema_version": "omp.worker-fault-check-report/v1",
        "passed": passed,
        "external_calls": 0,
        "policy_digest_sha256": SHA,
        "scenario_count": 28,
        "expected_reason_count": 28,
        "covered_reason_count": 28,
        "passed_scenarios": 28 if passed else 27,
        "failed_scenarios": 0 if passed else 1,
        "covered_reason_codes": ["verifier-accepted", "verifier-rejected"],
        "scenarios": [scenario],
        "quality_summary": {
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
    }


class WorkerFaultCheckCliTests(unittest.TestCase):
    def test_parser_accepts_policy_and_json_flag(self) -> None:
        arguments = build_parser().parse_args(
            ["worker", "fault-check", "policy.json", "--json"]
        )
        self.assertEqual(arguments.group, "worker")
        self.assertEqual(arguments.command, "fault-check")
        self.assertEqual(arguments.policy, Path("policy.json"))
        self.assertTrue(arguments.as_json)

    @patch("rolebench.cli.run_fault_check")
    def test_json_success_is_canonical_report(self, harness: object) -> None:
        report = fault_report()
        harness.return_value = report
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "worker",
                "fault-check",
                str(POLICY_PATH),
                "--json",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), canonical_json(report) + "\n")
        harness.assert_called_once_with(PRODUCT_ROOT, POLICY_PATH)

    @patch("rolebench.cli.run_fault_check")
    def test_plain_success_shows_gate_scenarios_coverage_and_accounting(
        self,
        harness: object,
    ) -> None:
        harness.return_value = fault_report()
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "worker",
                "fault-check",
                str(POLICY_PATH),
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        text = output.getvalue()
        self.assertIn("Worker fault check: PASS\n", text)
        self.assertIn(f"Policy SHA-256: {SHA}\n", text)
        self.assertIn("Reason-code coverage: 28/28 (28 scenarios)\n", text)
        self.assertIn("Covered reason codes: verifier-accepted, verifier-rejected\n", text)
        self.assertIn("External calls: 0\n", text)
        self.assertIn("[PASS] verifier-accepted:", text)
        self.assertIn("Scenarios passed: 28; failed: 0\n", text)
        self.assertIn("Quality score: 25.0% (1 accepted, 3 rejected)\n", text)
        self.assertIn(
            "Not scored: 18 system failures, 5 quarantined, 1 cancelled\n",
            text,
        )

    @patch("rolebench.cli.run_fault_check")
    def test_invalid_policy_prints_diagnostics_without_running_harness(
        self,
        harness: object,
    ) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        policy["network"]["direct_egress"] = True
        with tempfile.TemporaryDirectory() as temporary:
            invalid_path = Path(temporary) / "invalid-policy.json"
            invalid_path.write_text(canonical_json(policy) + "\n", encoding="utf-8")
            error = StringIO()
            status = run(
                [
                    "--root",
                    str(PRODUCT_ROOT),
                    "worker",
                    "fault-check",
                    str(invalid_path),
                ],
                stderr=error,
            )

        self.assertEqual(status, 1)
        self.assertIn("$.network.direct_egress", error.getvalue())
        harness.assert_not_called()

    @patch("rolebench.cli.run_fault_check")
    def test_failed_report_returns_one_and_prints_failure(self, harness: object) -> None:
        harness.return_value = fault_report(passed=False)
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "worker",
                "fault-check",
                str(POLICY_PATH),
            ],
            stdout=output,
        )

        self.assertEqual(status, 1)
        self.assertIn("Worker fault check: FAIL\n", output.getvalue())
        self.assertIn("[FAIL] verifier-accepted:", output.getvalue())
        self.assertIn('diagnostics: ["unexpected outcome"]', output.getvalue())
        self.assertIn("Scenarios passed: 27; failed: 1\n", output.getvalue())

    @patch("rolebench.cli.run_fault_check")
    def test_fault_harness_error_is_user_facing_status_two(self, harness: object) -> None:
        harness.side_effect = FaultHarnessError("synthetic observation is invalid")
        error = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "worker",
                "fault-check",
                str(POLICY_PATH),
            ],
            stderr=error,
        )

        self.assertEqual(status, 2)
        self.assertEqual(error.getvalue(), "error: synthetic observation is invalid\n")

    def test_existing_accounting_commands_remain_distinct_parser_routes(self) -> None:
        classify = build_parser().parse_args(
            ["accounting", "classify", "observation.json"]
        )
        summarize = build_parser().parse_args(
            ["accounting", "summarize", "outcome.json", "--json"]
        )

        self.assertEqual((classify.group, classify.command), ("accounting", "classify"))
        self.assertEqual(classify.path, Path("observation.json"))
        self.assertEqual((summarize.group, summarize.command), ("accounting", "summarize"))
        self.assertEqual(summarize.paths, [Path("outcome.json")])
        self.assertTrue(summarize.as_json)


if __name__ == "__main__":
    unittest.main()
