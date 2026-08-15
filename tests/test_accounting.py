from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from rolebench.accounting import AccountingError, classify_attempt, summarize_outcomes
from rolebench.cli import run
from rolebench.contracts import canonical_json, validate_artifact


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = PRODUCT_ROOT / "contracts"
SHA = "0" * 64


class AccountingFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(CONTRACTS_ROOT, self.root / "contracts")

    def write_json(self, name: str, value: dict[str, object]) -> Path:
        relative = Path(name)
        (self.root / relative).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return relative

    def observation(self) -> dict[str, object]:
        return {
            "schema_version": "omp.attempt-observation/v2",
            "observation_id": "observation-test",
            "observed_at": "2026-08-13T12:00:00Z",
            "attempt": {
                "attempt_id": "attempt-test",
                "number": 1,
                "previous_attempt_id": None,
            },
            "stage": "complete",
            "lifecycle": {
                "environment_started": True,
                "agent_started": True,
                "agent_finished": True,
                "artifact_frozen": True,
                "runner_started": True,
                "runner_finished": True,
                "runner_evidence_frozen": True,
                "verifier_started": True,
                "verifier_finished": True,
            },
            "readiness": {
                "environment": "ready",
                "runner": "healthy",
                "provider": "available",
            },
            "issues": [],
            "provider": {"request_started": True, "http_status": 200},
            "termination": {
                "kind": "completed",
                "exit_code": 0,
                "signal": None,
                "oom_scope": "none",
            },
            "verifier": {
                "outcome": "accepted",
                "result_valid": True,
                "reward": 1,
            },
            "integrity": {"state": "verified"},
            "digests": {
                "task": SHA,
                "config": SHA,
                "agent_image": SHA,
                "runner_image": SHA,
                "verifier_image": SHA,
                "runtime_policy": SHA,
                "artifact": SHA,
                "runner_evidence": SHA,
                "trajectory": SHA,
            },
        }
    def before_agent(self, issue: str, *, environment_started: bool) -> dict[str, object]:
        value = self.observation()
        value["stage"] = "environment"
        value["issues"] = [issue]
        value["lifecycle"] = {
            "environment_started": environment_started,
            "agent_started": False,
            "agent_finished": False,
            "artifact_frozen": False,
            "runner_started": False,
            "runner_finished": False,
            "runner_evidence_frozen": False,
            "verifier_started": False,
            "verifier_finished": False,
        }
        value["readiness"] = {
            "environment": "ready" if environment_started else "failed",
            "runner": "healthy",
            "provider": "unknown",
        }
        value["provider"] = {"request_started": False, "http_status": None}
        value["termination"] = {
            "kind": "unknown",
            "exit_code": None,
            "signal": None,
            "oom_scope": "none",
        }
        value["verifier"] = {
            "outcome": "not-run",
            "result_valid": False,
            "reward": None,
        }
        digests = value["digests"]
        self.assertIsInstance(digests, dict)
        digests["artifact"] = None
        digests["runner_evidence"] = None
        return value

    def provider_failure(self, issue: str, status: int | None) -> dict[str, object]:
        value = self.observation()
        value["stage"] = "agent"
        value["issues"] = [issue]
        lifecycle = value["lifecycle"]
        self.assertIsInstance(lifecycle, dict)
        lifecycle.update(
            {
                "artifact_frozen": False,
                "runner_started": False,
                "runner_finished": False,
                "runner_evidence_frozen": False,
                "verifier_started": False,
                "verifier_finished": False,
            }
        )
        readiness = value["readiness"]
        self.assertIsInstance(readiness, dict)
        readiness["provider"] = "failed"
        value["provider"] = {"request_started": True, "http_status": status}
        value["verifier"] = {
            "outcome": "not-run",
            "result_valid": False,
            "reward": None,
        }
        digests = value["digests"]
        self.assertIsInstance(digests, dict)
        digests["artifact"] = None
        digests["runner_evidence"] = None
        return value
    def verifier_failure(self, issue: str, *, finished: bool) -> dict[str, object]:
        value = self.observation()
        value["stage"] = "verifier"
        value["issues"] = [issue]
        lifecycle = value["lifecycle"]
        self.assertIsInstance(lifecycle, dict)
        lifecycle["verifier_finished"] = finished
        value["verifier"] = {
            "outcome": "error",
            "result_valid": False,
            "reward": None,
        }
        return value

    def stopped_agent(
        self,
        kind: str,
        *,
        oom_scope: str = "none",
        agent_finished: bool = True,
    ) -> dict[str, object]:
        value = self.observation()
        value["stage"] = "agent"
        lifecycle = value["lifecycle"]
        self.assertIsInstance(lifecycle, dict)
        lifecycle.update(
            {
                "agent_finished": agent_finished,
                "artifact_frozen": False,
                "runner_started": False,
                "runner_finished": False,
                "runner_evidence_frozen": False,
                "verifier_started": False,
                "verifier_finished": False,
            }
        )
        value["termination"] = {
            "kind": kind,
            "exit_code": 137 if kind == "resource-limit" else None,
            "signal": 9 if kind == "resource-limit" else None,
            "oom_scope": oom_scope,
        }
        value["verifier"] = {
            "outcome": "not-run",
            "result_valid": False,
            "reward": None,
        }
        digests = value["digests"]
        self.assertIsInstance(digests, dict)
        digests["artifact"] = None
        digests["runner_evidence"] = None
        return value
    def assert_valid_artifact(self, schema_name: str, value: dict[str, object]) -> None:
        result = validate_artifact(
            self.root,
            schema_name,
            self.write_json("artifact.json", value),
        )
        self.assertTrue(result.valid, result.diagnostics)


class AttemptClassificationTests(AccountingFixture):
    def test_fault_matrix_keeps_system_failures_out_of_model_quality(self) -> None:
        rejected = self.observation()
        rejected["verifier"] = {
            "outcome": "rejected",
            "result_valid": True,
            "reward": 0,
        }

        artifact_tampering = self.observation()
        artifact_tampering["issues"] = ["artifact-tampering"]
        artifact_tampering["integrity"] = {"state": "failed"}

        cases = (
            (
                "accepted",
                self.observation(),
                ("accepted", "scored", "none", True, "verifier-accepted"),
            ),
            (
                "verifier rejection",
                rejected,
                ("rejected", "scored", "model_task", True, "verifier-rejected"),
            ),
            (
                "missing reward",
                self.verifier_failure("verifier-result-missing", finished=True),
                ("no-valid-attempt", "retryable-invalid", "verifier", False, "verifier-result-missing"),
            ),
            (
                "malformed reward",
                self.verifier_failure("verifier-result-malformed", finished=True),
                ("no-valid-attempt", "retryable-invalid", "verifier", False, "verifier-result-malformed"),
            ),
            (
                "verifier crash",
                self.verifier_failure("verifier-crash", finished=False),
                ("no-valid-attempt", "retryable-invalid", "verifier", False, "verifier-crash"),
            ),
            (
                "image pull",
                self.before_agent("image-pull", environment_started=False),
                ("no-valid-attempt", "retryable-invalid", "environment_image", False, "image-pull"),
            ),
            (
                "broken entrypoint",
                self.before_agent("broken-entrypoint", environment_started=True),
                ("no-valid-attempt", "retryable-invalid", "environment_image", False, "broken-entrypoint"),
            ),
            (
                "dependency setup",
                self.before_agent("dependency-setup", environment_started=True),
                ("no-valid-attempt", "retryable-invalid", "dependency_download", False, "dependency-setup"),
            ),
            (
                "provider 429",
                self.provider_failure("provider-rate-limit", 429),
                ("no-valid-attempt", "retryable-invalid", "provider_api", False, "provider-rate-limit"),
            ),
            (
                "provider 503",
                self.provider_failure("provider-server-error", 503),
                ("no-valid-attempt", "retryable-invalid", "provider_api", False, "provider-server-error"),
            ),
            (
                "model deadline",
                self.stopped_agent("model-deadline"),
                ("rejected", "scored", "timeout_model_deadline", True, "model-deadline"),
            ),
            (
                "orchestrator timeout",
                self.stopped_agent("orchestrator-timeout", agent_finished=False),
                ("no-valid-attempt", "retryable-invalid", "timeout_orchestrator", False, "orchestrator-timeout"),
            ),
            (
                "attempt memory limit",
                self.stopped_agent("resource-limit", oom_scope="attempt"),
                ("rejected", "scored", "model_task", True, "attempt-resource-limit"),
            ),
            (
                "host memory exhaustion",
                self.stopped_agent("resource-limit", oom_scope="host", agent_finished=False),
                ("no-valid-attempt", "retryable-invalid", "runner_harness", False, "host-resource-exhaustion"),
            ),
            (
                "operator cancellation",
                self.stopped_agent("operator-cancelled", agent_finished=False),
                ("no-valid-attempt", "cancelled", "external_cancellation", False, "operator-cancelled"),
            ),
            (
                "runtime incompatibility",
                self.before_agent("runtime-incompatible", environment_started=False),
                ("no-valid-attempt", "retryable-invalid", "environment_image", False, "runtime-incompatible"),
            ),
            (
                "artifact tampering",
                artifact_tampering,
                ("no-valid-attempt", "quarantined", "integrity", False, "artifact-tampering"),
            ),
        )

        for name, observation, expected in cases:
            with self.subTest(case=name):
                self.assert_valid_artifact("attempt-observation", observation)
                outcome = classify_attempt(observation)
                self.assertEqual(
                    (
                        outcome["model_outcome"],
                        outcome["disposition"],
                        outcome["failure_domain"],
                        outcome["counts_toward_quality"],
                        outcome["reason_code"],
                    ),
                    expected,
                )
                self.assert_valid_artifact("attempt-outcome", outcome)

    def test_classification_is_deterministic_across_object_key_order(self) -> None:
        observation = self.observation()
        reordered = dict(reversed(tuple(observation.items())))
        self.assertEqual(classify_attempt(observation), classify_attempt(reordered))

    def test_observation_contract_rejects_rewardless_rejection(self) -> None:
        observation = self.observation()
        observation["verifier"] = {
            "outcome": "rejected",
            "result_valid": False,
            "reward": None,
        }
        result = validate_artifact(
            self.root,
            "attempt-observation",
            self.write_json("invalid-observation.json", observation),
        )
        self.assertFalse(result.valid)
        messages = "\n".join(item.message for item in result.diagnostics)
        self.assertIn("True was expected", messages)

    def test_public_classifier_fails_closed_on_malformed_verifier_result(self) -> None:
        observation = self.observation()
        observation["verifier"] = {
            "outcome": "accepted",
            "result_valid": False,
            "reward": None,
        }
        outcome = classify_attempt(observation)
        self.assertEqual(outcome["disposition"], "retryable-invalid")
        self.assertEqual(outcome["reason_code"], "verifier-result-malformed")
        self.assertEqual(outcome["verifier_outcome"], "error")
        self.assertFalse(outcome["counts_toward_quality"])

    def test_model_limit_without_provider_request_is_not_scored(self) -> None:
        observation = self.stopped_agent("model-deadline")
        observation["provider"] = {"request_started": False, "http_status": None}
        outcome = classify_attempt(observation)
        self.assertEqual(outcome["disposition"], "retryable-invalid")
        self.assertEqual(outcome["failure_domain"], "provider_api")
        self.assertEqual(outcome["reason_code"], "incomplete-observation")
        self.assertFalse(outcome["counts_toward_quality"])

    def test_verifier_error_overrides_model_limit_scoring(self) -> None:
        for termination_kind, oom_scope in (
            ("model-deadline", "none"),
            ("resource-limit", "attempt"),
        ):
            with self.subTest(termination=termination_kind):
                observation = self.stopped_agent(
                    termination_kind,
                    oom_scope=oom_scope,
                )
                observation["verifier"] = {
                    "outcome": "error",
                    "result_valid": False,
                    "reward": None,
                }
                self.assert_valid_artifact("attempt-observation", observation)
                outcome = classify_attempt(observation)
                self.assertEqual(outcome["disposition"], "retryable-invalid")
                self.assertEqual(outcome["failure_domain"], "verifier")
                self.assertEqual(outcome["reason_code"], "verifier-unhealthy")
                self.assertFalse(outcome["counts_toward_quality"])
                self.assert_valid_artifact("attempt-outcome", outcome)

    def test_cancellation_host_and_orchestrator_override_verifier_error(self) -> None:
        cases = (
            (
                self.stopped_agent("operator-cancelled", agent_finished=False),
                ("cancelled", "external_cancellation", "operator-cancelled"),
            ),
            (
                self.stopped_agent(
                    "resource-limit",
                    oom_scope="host",
                    agent_finished=False,
                ),
                (
                    "retryable-invalid",
                    "runner_harness",
                    "host-resource-exhaustion",
                ),
            ),
            (
                self.stopped_agent("orchestrator-timeout", agent_finished=False),
                (
                    "retryable-invalid",
                    "timeout_orchestrator",
                    "orchestrator-timeout",
                ),
            ),
        )
        for observation, expected in cases:
            with self.subTest(termination=expected[2]):
                observation["verifier"] = {
                    "outcome": "error",
                    "result_valid": False,
                    "reward": None,
                }
                outcome = classify_attempt(observation)
                self.assertEqual(
                    (
                        outcome["disposition"],
                        outcome["failure_domain"],
                        outcome["reason_code"],
                    ),
                    expected,
                )
                self.assertEqual(outcome["verifier_outcome"], "indeterminate")
                self.assert_valid_artifact("attempt-outcome", outcome)

    def test_verifier_failure_tuple_rejects_higher_precedence_termination(self) -> None:
        observation = self.stopped_agent("model-deadline")
        observation["verifier"] = {
            "outcome": "error",
            "result_valid": False,
            "reward": None,
        }
        valid_verifier_failure = classify_attempt(observation)
        contradictory_terminations = (
            {
                "kind": "operator-cancelled",
                "exit_code": None,
                "signal": None,
                "oom_scope": "none",
            },
            {
                "kind": "orchestrator-timeout",
                "exit_code": None,
                "signal": None,
                "oom_scope": "none",
            },
            {
                "kind": "resource-limit",
                "exit_code": 137,
                "signal": 9,
                "oom_scope": "host",
            },
        )
        for index, termination in enumerate(contradictory_terminations):
            with self.subTest(termination=termination["kind"]):
                outcome = deepcopy(valid_verifier_failure)
                outcome["termination"] = termination
                result = validate_artifact(
                    self.root,
                    "attempt-outcome",
                    self.write_json(f"invalid-verifier-precedence-{index}.json", outcome),
                )
                self.assertFalse(result.valid)
                with self.assertRaises(AccountingError):
                    summarize_outcomes([outcome])

    def test_non_host_failure_reasons_reject_host_resource_scope(self) -> None:
        image_failure = classify_attempt(
            self.before_agent("image-pull", environment_started=False)
        )
        incomplete_observation = self.stopped_agent("model-deadline")
        incomplete_observation["provider"] = {
            "request_started": False,
            "http_status": None,
        }
        incomplete = classify_attempt(incomplete_observation)

        for name, base in (
            ("image", image_failure),
            ("incomplete", incomplete),
        ):
            with self.subTest(reason=name):
                outcome = deepcopy(base)
                outcome["termination"] = {
                    "kind": "resource-limit",
                    "exit_code": 137,
                    "signal": 9,
                    "oom_scope": "host",
                }
                result = validate_artifact(
                    self.root,
                    "attempt-outcome",
                    self.write_json(f"invalid-host-scope-{name}.json", outcome),
                )
                self.assertFalse(result.valid)
                with self.assertRaises(AccountingError):
                    summarize_outcomes([outcome])

    def test_outcome_contract_rejects_unscored_quality_count(self) -> None:
        outcome = classify_attempt(self.observation())
        outcome["counts_toward_quality"] = False
        result = validate_artifact(
            self.root,
            "attempt-outcome",
            self.write_json("invalid-outcome.json", outcome),
        )
        self.assertFalse(result.valid)
        messages = "\n".join(item.message for item in result.diagnostics)
        self.assertIn("must be True for reason_code", messages)

    def test_outcome_contract_rejects_system_termination_as_scored_result(self) -> None:
        observation = self.observation()
        observation["verifier"] = {
            "outcome": "rejected",
            "result_valid": True,
            "reward": 0,
        }
        outcome = classify_attempt(observation)
        outcome["termination"] = {
            "kind": "orchestrator-timeout",
            "exit_code": None,
            "signal": None,
            "oom_scope": "none",
        }
        path = self.write_json("system-timeout-as-rejection.json", outcome)
        result = validate_artifact(self.root, "attempt-outcome", path)
        self.assertFalse(result.valid)
        messages = "\n".join(
            f"{item.json_path}: {item.message}" for item in result.diagnostics
        )
        self.assertIn("$.termination.kind", messages)
        self.assertIn("verifier-rejected", messages)

        error = StringIO()
        status = run(
            [
                "--root",
                str(self.root),
                "accounting",
                "summarize",
                str(path),
            ],
            stderr=error,
        )
        self.assertEqual(status, 1)
        self.assertIn("$.termination.kind", error.getvalue())

    def test_outcome_contract_rejects_contradictory_unscored_tuples(self) -> None:
        retryable = classify_attempt(
            self.provider_failure("provider-rate-limit", 429)
        )
        retryable["reason_code"] = "verifier-rejected"

        quarantine_observation = self.observation()
        quarantine_observation["issues"] = ["artifact-tampering"]
        quarantine_observation["integrity"] = {"state": "failed"}
        quarantined = classify_attempt(quarantine_observation)
        quarantined["failure_domain"] = "provider_api"

        cancelled = classify_attempt(
            self.stopped_agent("operator-cancelled", agent_finished=False)
        )
        termination = cancelled["termination"]
        self.assertIsInstance(termination, dict)
        termination["kind"] = "completed"

        for name, outcome in (
            ("retryable", retryable),
            ("quarantined", quarantined),
            ("cancelled", cancelled),
        ):
            with self.subTest(disposition=name):
                result = validate_artifact(
                    self.root,
                    "attempt-outcome",
                    self.write_json(f"invalid-{name}.json", outcome),
                )
                self.assertFalse(result.valid)

    def test_public_summary_rejects_contradictory_unscored_tuple(self) -> None:
        outcome = classify_attempt(
            self.provider_failure("provider-rate-limit", 429)
        )
        outcome["reason_code"] = "verifier-rejected"
        with self.assertRaises(AccountingError):
            summarize_outcomes([outcome])


class ScoreSummaryTests(AccountingFixture):
    def test_quality_uses_only_valid_accepted_and_rejected_attempts(self) -> None:
        accepted = classify_attempt(self.observation())

        rejected_observation = self.observation()
        rejected_observation["verifier"] = {
            "outcome": "rejected",
            "result_valid": True,
            "reward": 0,
        }
        rejected = classify_attempt(rejected_observation)
        system_failure = classify_attempt(
            self.provider_failure("provider-rate-limit", 429)
        )
        cancelled = classify_attempt(
            self.stopped_agent("operator-cancelled", agent_finished=False)
        )
        tampering_observation = self.observation()
        tampering_observation["issues"] = ["artifact-tampering"]
        tampering_observation["integrity"] = {"state": "failed"}
        quarantined = classify_attempt(tampering_observation)

        summary = summarize_outcomes(
            [accepted, rejected, system_failure, cancelled, quarantined]
        )
        self.assertEqual(
            summary,
            {
                "total_attempts": 5,
                "scored_attempts": 2,
                "accepted": 1,
                "rejected": 1,
                "quality_score": 0.5,
                "not_scored": {
                    "retryable_invalid": 1,
                    "quarantined": 1,
                    "cancelled": 1,
                    "excluded": 0,
                },
            },
        )

    def test_admission_and_calibration_evidence_is_never_scored(
        self,
    ) -> None:
        for evidence_use in (
            "admission-only",
            "calibration-only",
        ):
            observation = self.observation()
            observation["evidence_use"] = evidence_use
            digests = observation["digests"]
            self.assertIsInstance(digests, dict)
            digests.update(
                {
                    "task_public_tree": SHA,
                    "verifier_private_tree": SHA,
                    "agent_image_config": SHA,
                    "runner_image_config": SHA,
                    "verifier_image_config": SHA,
                }
            )
            if evidence_use == "calibration-only":
                digests["qualification"] = SHA

            outcome = classify_attempt(observation)

            self.assertEqual(
                outcome["reason_code"],
                "non-scored-evidence",
            )
            self.assertEqual(outcome["disposition"], "excluded")
            self.assertIs(
                outcome["counts_toward_quality"],
                False,
            )
            self.assertEqual(
                summarize_outcomes([outcome])["not_scored"][
                    "excluded"
                ],
                1,
            )

    def test_v1_admission_evidence_remains_replayable(self) -> None:
        observation = self.observation()
        observation["schema_version"] = "omp.attempt-observation/v1"
        observation["evidence_use"] = "admission-only"
        lifecycle = observation["lifecycle"]
        self.assertIsInstance(lifecycle, dict)
        for field in (
            "runner_started",
            "runner_finished",
            "runner_evidence_frozen",
        ):
            lifecycle.pop(field)
        digests = observation["digests"]
        self.assertIsInstance(digests, dict)
        digests.pop("runner_image")
        digests.pop("runner_evidence")
        digests.update(
            {
                "task_public_tree": SHA,
                "verifier_private_tree": SHA,
                "agent_image_config": SHA,
                "verifier_image_config": SHA,
            }
        )

        self.assert_valid_artifact("attempt-observation", observation)
        outcome = classify_attempt(observation)

        self.assertEqual(
            outcome["schema_version"],
            "omp.attempt-outcome/v1",
        )
        self.assertEqual(outcome["reason_code"], "non-scored-evidence")
        self.assertEqual(outcome["disposition"], "excluded")
        self.assertFalse(outcome["counts_toward_quality"])
        self.assert_valid_artifact("attempt-outcome", outcome)

    def test_provider_disabled_admission_and_calibration_runs_are_excluded(
        self,
    ) -> None:
        for evidence_use in (
            "admission-only",
            "calibration-only",
        ):
            with self.subTest(evidence_use=evidence_use):
                observation = self.observation()
                observation["evidence_use"] = evidence_use
                readiness = observation["readiness"]
                self.assertIsInstance(readiness, dict)
                readiness["provider"] = "unknown"
                observation["provider"] = {
                    "request_started": False,
                    "http_status": None,
                }
                digests = observation["digests"]
                self.assertIsInstance(digests, dict)
                digests.update(
                    {
                        "task_public_tree": SHA,
                        "verifier_private_tree": SHA,
                        "agent_image_config": SHA,
                        "runner_image_config": SHA,
                        "verifier_image_config": SHA,
                    }
                )
                if evidence_use == "calibration-only":
                    digests["qualification"] = SHA

                self.assert_valid_artifact("attempt-observation", observation)
                outcome = classify_attempt(observation)

                self.assertEqual(
                    outcome["reason_code"],
                    "non-scored-evidence",
                )
                self.assertEqual(outcome["disposition"], "excluded")
                self.assertEqual(outcome["verifier_outcome"], "indeterminate")
                self.assertFalse(outcome["counts_toward_quality"])
                self.assert_valid_artifact("attempt-outcome", outcome)


    def test_non_scored_admission_and_calibration_model_limits_and_resource_limits_validate_and_exclude(
        self,
    ) -> None:
        for evidence_use in (
            "admission-only",
            "calibration-only",
        ):
            for kind, oom_scope in (
                ("model-deadline", "none"),
                ("resource-limit", "attempt"),
            ):
                with self.subTest(evidence_use=evidence_use, kind=kind, oom_scope=oom_scope):
                    observation = self.stopped_agent(kind, oom_scope=oom_scope)
                    observation["evidence_use"] = evidence_use
                    observation["provider"] = {
                        "request_started": False,
                        "http_status": None,
                    }
                    readiness = observation["readiness"]
                    self.assertIsInstance(readiness, dict)
                    readiness["provider"] = "unknown"
                    digests = observation["digests"]
                    self.assertIsInstance(digests, dict)
                    digests.update(
                        {
                            "task_public_tree": SHA,
                            "verifier_private_tree": SHA,
                            "agent_image_config": SHA,
                            "runner_image_config": SHA,
                            "verifier_image_config": SHA,
                        }
                    )
                    if evidence_use == "calibration-only":
                        digests["qualification"] = SHA

                    self.assert_valid_artifact("attempt-observation", observation)
                    outcome = classify_attempt(observation)
                    self.assertEqual(outcome["disposition"], "excluded")
                    self.assertEqual(outcome["reason_code"], "non-scored-evidence")
                    self.assertEqual(outcome["model_outcome"], "no-valid-attempt")
                    self.assertEqual(outcome["verifier_outcome"], "indeterminate")
                    self.assertFalse(outcome["counts_toward_quality"])
                    self.assert_valid_artifact("attempt-outcome", outcome)

    def test_scored_model_limit_requires_started_provider_request(self) -> None:
        for kind, oom_scope in (
            ("model-deadline", "none"),
            ("resource-limit", "attempt"),
        ):
            with self.subTest(kind=kind, oom_scope=oom_scope):
                observation = self.stopped_agent(kind, oom_scope=oom_scope)
                observation["provider"] = {
                    "request_started": False,
                    "http_status": None,
                }
                result = validate_artifact(
                    self.root,
                    "attempt-observation",
                    self.write_json("unstarted-provider-observation.json", observation),
                )
                self.assertFalse(result.valid)
                messages = "\n".join(item.message for item in result.diagnostics)
                self.assertIn(
                    "a scored model limit requires a started provider request",
                    messages,
                )
                outcome = classify_attempt(observation)
                self.assertEqual(outcome["disposition"], "retryable-invalid")
                self.assertEqual(outcome["reason_code"], "incomplete-observation")
                self.assertEqual(outcome["failure_domain"], "provider_api")
    def test_no_decisive_result_has_no_quality_score(self) -> None:
        outcome = classify_attempt(
            self.provider_failure("provider-server-error", 503)
        )
        summary = summarize_outcomes([outcome])
        self.assertIsNone(summary["quality_score"])
        self.assertEqual(summary["scored_attempts"], 0)

    def test_accounting_cli_classifies_and_summarizes(self) -> None:
        observation_path = self.write_json("observation.json", self.observation())
        classified = StringIO()
        status = run(
            [
                "--root",
                str(self.root),
                "accounting",
                "classify",
                str(observation_path),
            ],
            stdout=classified,
        )
        self.assertEqual(status, 0)
        outcome = json.loads(classified.getvalue())
        self.assertEqual(outcome["model_outcome"], "accepted")
        self.assertEqual(classified.getvalue(), canonical_json(outcome) + "\n")

        outcome_path = self.write_json("outcome.json", outcome)
        summarized = StringIO()
        status = run(
            [
                "--root",
                str(self.root),
                "accounting",
                "summarize",
                str(outcome_path),
                "--json",
            ],
            stdout=summarized,
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(summarized.getvalue()),
            {
                "total_attempts": 1,
                "scored_attempts": 1,
                "accepted": 1,
                "rejected": 0,
                "quality_score": 1.0,
                "not_scored": {
                    "retryable_invalid": 0,
                    "quarantined": 0,
                    "cancelled": 0,
                    "excluded": 0,
                },
            },
        )
