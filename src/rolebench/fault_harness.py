"""Deterministic, no-model conformance checks for scored-worker accounting."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from .accounting import AccountingError, classify_attempt, summarize_outcomes
from .accounting_rules import ATTEMPT_OUTCOME_RULES
from .contracts import (
    ContractError,
    JSONObject,
    JSONValue,
    canonical_json,
    validate_artifact,
)


class FaultHarnessError(ValueError):
    """The fault harness cannot safely validate its policy or artifacts."""


# These expectations are deliberately independent of ATTEMPT_OUTCOME_RULES and the
# classifier.  The rule table is consulted only for an exact reason-code coverage check.
_EXPECTED_OUTCOMES: dict[str, tuple[str, str, str, str, str, bool]] = {
    "verifier-accepted": (
        "verifier-accepted",
        "scored",
        "none",
        "accepted",
        "accepted",
        True,
    ),
    "verifier-rejected": (
        "verifier-rejected",
        "scored",
        "model_task",
        "rejected",
        "rejected",
        True,
    ),
    "model-deadline": (
        "model-deadline",
        "scored",
        "timeout_model_deadline",
        "rejected",
        "indeterminate",
        True,
    ),
    "attempt-resource-limit": (
        "attempt-resource-limit",
        "scored",
        "model_task",
        "rejected",
        "indeterminate",
        True,
    ),
    "operator-cancelled": (
        "operator-cancelled",
        "cancelled",
        "external_cancellation",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "non-scored-evidence": (
        "non-scored-evidence",
        "excluded",
        "experiment_scope",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "suspected-reward-hacking": (
        "suspected-reward-hacking",
        "quarantined",
        "suspected_reward_hacking",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "sandbox-violation": (
        "sandbox-violation",
        "quarantined",
        "policy_sandbox",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "artifact-tampering": (
        "artifact-tampering",
        "quarantined",
        "integrity",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "integrity-failed": (
        "integrity-failed",
        "quarantined",
        "integrity",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "integrity-unknown": (
        "integrity-unknown",
        "quarantined",
        "integrity",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "environment-startup": (
        "environment-startup",
        "retryable-invalid",
        "environment_image",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "image-pull": (
        "image-pull",
        "retryable-invalid",
        "environment_image",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "broken-entrypoint": (
        "broken-entrypoint",
        "retryable-invalid",
        "environment_image",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "dependency-setup": (
        "dependency-setup",
        "retryable-invalid",
        "dependency_download",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "runner-failure": (
        "runner-failure",
        "retryable-invalid",
        "runner_harness",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "artifact-collection": (
        "artifact-collection",
        "retryable-invalid",
        "runner_harness",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "provider-rate-limit": (
        "provider-rate-limit",
        "retryable-invalid",
        "provider_api",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "provider-server-error": (
        "provider-server-error",
        "retryable-invalid",
        "provider_api",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "provider-auth-error": (
        "provider-auth-error",
        "retryable-invalid",
        "provider_api",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "provider-network-error": (
        "provider-network-error",
        "retryable-invalid",
        "provider_api",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "runtime-incompatible": (
        "runtime-incompatible",
        "retryable-invalid",
        "environment_image",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "host-resource-exhaustion": (
        "host-resource-exhaustion",
        "retryable-invalid",
        "runner_harness",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "orchestrator-timeout": (
        "orchestrator-timeout",
        "retryable-invalid",
        "timeout_orchestrator",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
    "verifier-crash": (
        "verifier-crash",
        "retryable-invalid",
        "verifier",
        "no-valid-attempt",
        "error",
        False,
    ),
    "verifier-result-missing": (
        "verifier-result-missing",
        "retryable-invalid",
        "verifier",
        "no-valid-attempt",
        "error",
        False,
    ),
    "verifier-result-malformed": (
        "verifier-result-malformed",
        "retryable-invalid",
        "verifier",
        "no-valid-attempt",
        "error",
        False,
    ),
    "verifier-unhealthy": (
        "verifier-unhealthy",
        "retryable-invalid",
        "verifier",
        "no-valid-attempt",
        "error",
        False,
    ),
    "incomplete-observation": (
        "incomplete-observation",
        "retryable-invalid",
        "runner_harness",
        "no-valid-attempt",
        "indeterminate",
        False,
    ),
}

_EXPECTED_SUMMARY: JSONObject = {
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
}
_SHA = "0" * 64


def _diagnostics(result: object) -> list[str]:
    raw = getattr(result, "diagnostics", ())
    return [f"{item.json_path}: {item.message}" for item in raw]


def _expected_object(
    values: tuple[str, str, str, str, str, bool]
) -> JSONObject:
    reason, disposition, domain, model, verifier, counted = values
    return {
        "reason_code": reason,
        "disposition": disposition,
        "failure_domain": domain,
        "model_outcome": model,
        "verifier_outcome": verifier,
        "counts_toward_quality": counted,
    }


def _actual_object(outcome: JSONObject | None) -> JSONObject:
    fields = (
        "reason_code",
        "disposition",
        "failure_domain",
        "model_outcome",
        "verifier_outcome",
        "counts_toward_quality",
    )
    return {
        field: outcome.get(field) if outcome is not None else None
        for field in fields
    }


def _base_observation(scenario: str, policy_digest: str) -> JSONObject:
    return {
        "schema_version": "omp.attempt-observation/v2",
        "observation_id": f"fault-check-{scenario}",
        "observed_at": "2026-08-13T12:00:00Z",
        "attempt": {
            "attempt_id": f"fault-check-{scenario}",
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
            "task": _SHA,
            "config": _SHA,
            "agent_image": _SHA,
            "runner_image": _SHA,
            "verifier_image": _SHA,
            "runtime_policy": policy_digest,
            "artifact": _SHA,
            "runner_evidence": _SHA,
            "trajectory": _SHA,
        },
    }


def _stopped(
    observation: JSONObject, kind: str, oom_scope: str = "none"
) -> None:
    observation["stage"] = "agent"
    lifecycle = cast(JSONObject, observation["lifecycle"])
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
    observation["termination"] = {
        "kind": kind,
        "exit_code": 137 if kind == "resource-limit" else None,
        "signal": None,
        "oom_scope": oom_scope,
    }
    observation["verifier"] = {
        "outcome": "not-run",
        "result_valid": False,
        "reward": None,
    }
    cast(JSONObject, observation["digests"])["artifact"] = None
    cast(JSONObject, observation["digests"])["runner_evidence"] = None


def _observation(scenario: str, policy_digest: str) -> JSONObject:
    value = _base_observation(scenario, policy_digest)

    if scenario == "non-scored-evidence":
        value["evidence_use"] = "admission-only"
        digests = cast(JSONObject, value["digests"])
        digests.update(
            {
                "task_public_tree": _SHA,
                "verifier_private_tree": _SHA,
                "agent_image_config": _SHA,
                "runner_image_config": _SHA,
                "verifier_image_config": _SHA,
            }
        )
        return value
    if scenario == "verifier-rejected":
        value["verifier"] = {
            "outcome": "rejected",
            "result_valid": True,
            "reward": 0,
        }
    elif scenario in {
        "model-deadline",
        "orchestrator-timeout",
        "operator-cancelled",
    }:
        _stopped(value, scenario)
    elif scenario == "attempt-resource-limit":
        _stopped(value, "resource-limit", "attempt")
    elif scenario == "host-resource-exhaustion":
        _stopped(value, "resource-limit", "host")
    elif scenario == "incomplete-observation":
        _stopped(value, "signal")
        cast(JSONObject, value["termination"])["signal"] = 9
    elif scenario in {
        "suspected-reward-hacking",
        "sandbox-violation",
        "artifact-tampering",
    }:
        value["issues"] = [scenario]
        if scenario == "artifact-tampering":
            cast(JSONObject, value["integrity"])["state"] = "failed"
    elif scenario in {"integrity-failed", "integrity-unknown"}:
        cast(JSONObject, value["integrity"])["state"] = (
            scenario.removeprefix("integrity-")
        )
    elif scenario in {
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
    }:
        value["issues"] = [scenario]
        status: JSONValue = 200
        if scenario == "provider-rate-limit":
            status = 429
        elif scenario == "provider-server-error":
            status = 503
        elif scenario == "provider-auth-error":
            status = 401
        cast(JSONObject, value["provider"])["http_status"] = status
        if scenario in {
            "environment-startup",
            "image-pull",
            "broken-entrypoint",
            "dependency-setup",
            "runtime-incompatible",
        }:
            value["stage"] = "environment"
            cast(JSONObject, value["lifecycle"]).update(
                {
                    "agent_started": False,
                    "agent_finished": False,
                    "artifact_frozen": False,
                    "runner_started": False,
                    "runner_finished": False,
                    "runner_evidence_frozen": False,
                    "verifier_started": False,
                    "verifier_finished": False,
                }
            )
            if scenario in {
                "environment-startup",
                "image-pull",
                "runtime-incompatible",
            }:
                cast(JSONObject, value["lifecycle"])[
                    "environment_started"
                ] = False
                cast(JSONObject, value["readiness"])["environment"] = "failed"
            value["provider"] = {
                "request_started": False,
                "http_status": None,
            }
            value["verifier"] = {
                "outcome": "not-run",
                "result_valid": False,
                "reward": None,
            }
            cast(JSONObject, value["digests"])["artifact"] = None
            cast(JSONObject, value["digests"])["runner_evidence"] = None
        elif scenario in {
            "provider-rate-limit",
            "provider-server-error",
            "provider-auth-error",
            "provider-network-error",
        }:
            value["stage"] = "agent"
            cast(JSONObject, value["lifecycle"]).update(
                {
                    "artifact_frozen": False,
                    "runner_started": False,
                    "runner_finished": False,
                    "runner_evidence_frozen": False,
                    "verifier_started": False,
                    "verifier_finished": False,
                }
            )
            cast(JSONObject, value["readiness"])["provider"] = "failed"
            value["verifier"] = {
                "outcome": "not-run",
                "result_valid": False,
                "reward": None,
            }
            cast(JSONObject, value["digests"])["artifact"] = None
            cast(JSONObject, value["digests"])["runner_evidence"] = None
        elif scenario in {"runner-failure", "artifact-collection"}:
            cast(JSONObject, value["readiness"])["runner"] = "failed"
            value["verifier"] = {
                "outcome": "not-run",
                "result_valid": False,
                "reward": None,
            }
            cast(JSONObject, value["digests"])["artifact"] = None
            cast(JSONObject, value["digests"])["runner_evidence"] = None
            cast(JSONObject, value["lifecycle"]).update(
                {
                    "artifact_frozen": False,
                    "runner_started": False,
                    "runner_finished": False,
                    "runner_evidence_frozen": False,
                    "verifier_started": False,
                    "verifier_finished": False,
                }
            )
    elif scenario in {
        "verifier-crash",
        "verifier-result-missing",
        "verifier-result-malformed",
    }:
        value["issues"] = [scenario]
        value["stage"] = "verifier"
        value["verifier"] = {
            "outcome": "error",
            "result_valid": False,
            "reward": None,
        }
        if scenario == "verifier-crash":
            cast(JSONObject, value["lifecycle"])["verifier_finished"] = False
    elif scenario == "verifier-unhealthy":
        value["stage"] = "verifier"
        value["verifier"] = {
            "outcome": "error",
            "result_valid": False,
            "reward": None,
        }
    elif scenario != "verifier-accepted":
        raise FaultHarnessError(
            f"no synthetic observation generator for {scenario!r}"
        )

    return value


def _write_artifact(directory: Path, name: str, value: JSONObject) -> Path:
    path = directory / f"{name}.json"
    try:
        path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    except OSError as error:
        raise FaultHarnessError(
            f"cannot write synthetic artifact {name!r}: {error}"
        ) from error
    return path


def _load_validated_policy(
    root: Path, policy_path: Path
) -> tuple[JSONObject, str]:
    try:
        result = validate_artifact(root, "scored-worker-policy", policy_path)
    except ContractError as error:
        raise FaultHarnessError(str(error)) from error
    if not result.valid:
        details = "; ".join(_diagnostics(result))
        raise FaultHarnessError(f"invalid scored-worker policy: {details}")

    resolved = policy_path.expanduser()
    if not resolved.is_absolute():
        resolved = root / resolved
    try:
        decoded: object = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FaultHarnessError(
            f"cannot read validated scored-worker policy: {error}"
        ) from error
    if not isinstance(decoded, dict):
        raise FaultHarnessError(
            "validated scored-worker policy is not a JSON object"
        )
    policy = cast(JSONObject, decoded)
    if policy.get("schema_version") != "omp.scored-worker-policy/v2":
        raise FaultHarnessError(
            "fault check requires omp.scored-worker-policy/v2"
        )
    try:
        digest = sha256(canonical_json(policy).encode("utf-8")).hexdigest()
    except (TypeError, ValueError) as error:
        raise FaultHarnessError(
            f"cannot canonicalize scored-worker policy: {error}"
        ) from error
    return policy, digest


def run_fault_check(root: Path, policy_path: Path) -> JSONObject:
    """Execute all synthetic attempt observations against the classifier and policy."""
    root = Path(root).resolve()
    policy, policy_digest = _load_validated_policy(root, policy_path)

    scenarios: list[JSONObject] = []
    outcomes: list[JSONObject] = []
    failed_scenarios = 0

    with TemporaryDirectory(prefix="rolebench-fault-check-") as temporary:
        temp_dir = Path(temporary)
        for scenario_name, expected in _EXPECTED_OUTCOMES.items():
            expected_obj = _expected_object(expected)
            observation = _observation(scenario_name, policy_digest)
            observation_path = _write_artifact(
                temp_dir, f"{scenario_name}-obs", observation
            )
            obs_validation = validate_artifact(
                root, "attempt-observation", observation_path
            )
            obs_valid = obs_validation.valid

            outcome_diagnostics: list[str] = []
            try:
                outcome = classify_attempt(observation)
                outcome_path = _write_artifact(
                    temp_dir, f"{scenario_name}-out", outcome
                )
                outcome_validation = validate_artifact(
                    root, "attempt-outcome", outcome_path
                )
                outcome_valid = outcome_validation.valid
                if not outcome_valid:
                    outcome_diagnostics.extend(_diagnostics(outcome_validation))
                actual_obj = _actual_object(outcome)
                if outcome_valid:
                    outcomes.append(outcome)
            except AccountingError as exc:
                outcome_valid = False
                actual_obj = {
                    "reason_code": "accounting-error",
                    "disposition": "accounting-error",
                    "model_outcome": "accounting-error",
                    "verifier_outcome": "accounting-error",
                    "counts_toward_quality": False,
                }
                outcome_diagnostics.append(f"classification error: {exc}")

            passed = (
                obs_valid
                and outcome_valid
                and actual_obj == expected_obj
            )
            if not passed:
                failed_scenarios += 1

            diagnostics_list: list[str] = []
            if not obs_valid:
                diagnostics_list.extend(_diagnostics(obs_validation))
            if not outcome_valid:
                diagnostics_list.extend(outcome_diagnostics)
            if actual_obj != expected_obj:
                diagnostics_list.append(
                    f"outcome mismatch: actual {actual_obj} != expected {expected_obj}"
                )

            scenarios.append(
                {
                    "scenario": scenario_name,
                    "passed": passed,
                    "observation_valid": obs_valid,
                    "outcome_valid": outcome_valid,
                    "expected": expected_obj,
                    "actual": actual_obj,
                    "diagnostics": diagnostics_list,
                }
            )

    summary = summarize_outcomes(outcomes)
    summary_matches_expected = summary == _EXPECTED_SUMMARY
    expected_reasons = set(ATTEMPT_OUTCOME_RULES)
    covered_reasons = set(_EXPECTED_OUTCOMES)
    covered = sorted(covered_reasons)
    missing_reason_codes = sorted(expected_reasons - covered_reasons)
    unexpected_reason_codes = sorted(covered_reasons - expected_reasons)
    coverage_complete = not missing_reason_codes and not unexpected_reason_codes
    diagnostics = (
        []
        if summary_matches_expected
        else [
            "quality summary mismatch: "
            f"expected {canonical_json(_EXPECTED_SUMMARY)}, "
            f"actual {canonical_json(summary)}"
        ]
    )

    return {
        "schema_version": "omp.worker-fault-check-report/v1",
        "passed": (
            failed_scenarios == 0
            and coverage_complete
            and summary_matches_expected
        ),
        "external_calls": 0,
        "policy_digest_sha256": policy_digest,
        "scenario_count": len(_EXPECTED_OUTCOMES),
        "expected_reason_count": len(ATTEMPT_OUTCOME_RULES),
        "covered_reason_count": len(covered),
        "passed_scenarios": len(_EXPECTED_OUTCOMES) - failed_scenarios,
        "failed_scenarios": failed_scenarios,
        "covered_reason_codes": covered,
        "missing_reason_codes": missing_reason_codes,
        "unexpected_reason_codes": unexpected_reason_codes,
        "quality_summary": summary,
        "diagnostics": diagnostics,
        "scenarios": scenarios,
    }
