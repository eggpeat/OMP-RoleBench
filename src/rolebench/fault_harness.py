"""Deterministic, no-model conformance checks for scored-worker accounting."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from .accounting import AccountingError, classify_attempt, summarize_outcomes
from .accounting_rules import ATTEMPT_OUTCOME_RULES
from .contracts import ContractError, JSONObject, JSONValue, canonical_json, validate_artifact


class FaultHarnessError(ValueError):
    """The fault harness cannot safely validate its policy or artifacts."""


# These expectations are deliberately independent of ATTEMPT_OUTCOME_RULES and the
# classifier.  The rule table is consulted only for an exact reason-code coverage check.
_EXPECTED_OUTCOMES: dict[str, tuple[str, str, str, str, str, bool]] = {
    "verifier-accepted": ("verifier-accepted", "scored", "none", "accepted", "accepted", True),
    "verifier-rejected": ("verifier-rejected", "scored", "model_task", "rejected", "rejected", True),
    "model-deadline": ("model-deadline", "scored", "timeout_model_deadline", "rejected", "indeterminate", True),
    "attempt-resource-limit": ("attempt-resource-limit", "scored", "model_task", "rejected", "indeterminate", True),
    "operator-cancelled": ("operator-cancelled", "cancelled", "external_cancellation", "no-valid-attempt", "indeterminate", False),
    "suspected-reward-hacking": ("suspected-reward-hacking", "quarantined", "suspected_reward_hacking", "no-valid-attempt", "indeterminate", False),
    "sandbox-violation": ("sandbox-violation", "quarantined", "policy_sandbox", "no-valid-attempt", "indeterminate", False),
    "artifact-tampering": ("artifact-tampering", "quarantined", "integrity", "no-valid-attempt", "indeterminate", False),
    "integrity-failed": ("integrity-failed", "quarantined", "integrity", "no-valid-attempt", "indeterminate", False),
    "integrity-unknown": ("integrity-unknown", "quarantined", "integrity", "no-valid-attempt", "indeterminate", False),
    "environment-startup": ("environment-startup", "retryable-invalid", "environment_image", "no-valid-attempt", "indeterminate", False),
    "image-pull": ("image-pull", "retryable-invalid", "environment_image", "no-valid-attempt", "indeterminate", False),
    "broken-entrypoint": ("broken-entrypoint", "retryable-invalid", "environment_image", "no-valid-attempt", "indeterminate", False),
    "dependency-setup": ("dependency-setup", "retryable-invalid", "dependency_download", "no-valid-attempt", "indeterminate", False),
    "runner-failure": ("runner-failure", "retryable-invalid", "runner_harness", "no-valid-attempt", "indeterminate", False),
    "artifact-collection": ("artifact-collection", "retryable-invalid", "runner_harness", "no-valid-attempt", "indeterminate", False),
    "provider-rate-limit": ("provider-rate-limit", "retryable-invalid", "provider_api", "no-valid-attempt", "indeterminate", False),
    "provider-server-error": ("provider-server-error", "retryable-invalid", "provider_api", "no-valid-attempt", "indeterminate", False),
    "provider-auth-error": ("provider-auth-error", "retryable-invalid", "provider_api", "no-valid-attempt", "indeterminate", False),
    "provider-network-error": ("provider-network-error", "retryable-invalid", "provider_api", "no-valid-attempt", "indeterminate", False),
    "runtime-incompatible": ("runtime-incompatible", "retryable-invalid", "environment_image", "no-valid-attempt", "indeterminate", False),
    "host-resource-exhaustion": ("host-resource-exhaustion", "retryable-invalid", "runner_harness", "no-valid-attempt", "indeterminate", False),
    "orchestrator-timeout": ("orchestrator-timeout", "retryable-invalid", "timeout_orchestrator", "no-valid-attempt", "indeterminate", False),
    "verifier-crash": ("verifier-crash", "retryable-invalid", "verifier", "no-valid-attempt", "error", False),
    "verifier-result-missing": ("verifier-result-missing", "retryable-invalid", "verifier", "no-valid-attempt", "error", False),
    "verifier-result-malformed": ("verifier-result-malformed", "retryable-invalid", "verifier", "no-valid-attempt", "error", False),
    "verifier-unhealthy": ("verifier-unhealthy", "retryable-invalid", "verifier", "no-valid-attempt", "error", False),
    "incomplete-observation": ("incomplete-observation", "retryable-invalid", "runner_harness", "no-valid-attempt", "indeterminate", False),
}

_EXPECTED_SUMMARY: JSONObject = {
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
}
_SHA = "0" * 64


def _diagnostics(result: object) -> list[str]:
    raw = getattr(result, "diagnostics", ())
    return [f"{item.json_path}: {item.message}" for item in raw]


def _expected_object(values: tuple[str, str, str, str, str, bool]) -> JSONObject:
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
    return {field: outcome.get(field) if outcome is not None else None for field in fields}


def _base_observation(scenario: str, policy_digest: str) -> JSONObject:
    return {
        "schema_version": "omp.attempt-observation/v1",
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
        "verifier": {"outcome": "accepted", "result_valid": True, "reward": 1},
        "integrity": {"state": "verified"},
        "digests": {
            "task": _SHA,
            "config": _SHA,
            "agent_image": _SHA,
            "verifier_image": _SHA,
            "runtime_policy": policy_digest,
            "artifact": _SHA,
            "trajectory": _SHA,
        },
    }


def _stopped(observation: JSONObject, kind: str, oom_scope: str = "none") -> None:
    observation["stage"] = "agent"
    lifecycle = cast(JSONObject, observation["lifecycle"])
    lifecycle.update({
        "artifact_frozen": False,
        "verifier_started": False,
        "verifier_finished": False,
    })
    observation["termination"] = {
        "kind": kind,
        "exit_code": 137 if kind == "resource-limit" else None,
        "signal": None,
        "oom_scope": oom_scope,
    }
    observation["verifier"] = {"outcome": "not-run", "result_valid": False, "reward": None}
    cast(JSONObject, observation["digests"])["artifact"] = None


def _observation(scenario: str, policy_digest: str) -> JSONObject:
    value = _base_observation(scenario, policy_digest)

    if scenario == "verifier-rejected":
        value["verifier"] = {"outcome": "rejected", "result_valid": True, "reward": 0}
    elif scenario in {"model-deadline", "orchestrator-timeout", "operator-cancelled"}:
        _stopped(value, scenario)
    elif scenario == "attempt-resource-limit":
        _stopped(value, "resource-limit", "attempt")
    elif scenario == "host-resource-exhaustion":
        _stopped(value, "resource-limit", "host")
    elif scenario == "incomplete-observation":
        _stopped(value, "signal")
        cast(JSONObject, value["termination"])["signal"] = 9
    elif scenario in {"suspected-reward-hacking", "sandbox-violation", "artifact-tampering"}:
        value["issues"] = [scenario]
        if scenario == "artifact-tampering":
            cast(JSONObject, value["integrity"])["state"] = "failed"
    elif scenario in {"integrity-failed", "integrity-unknown"}:
        cast(JSONObject, value["integrity"])["state"] = scenario.removeprefix("integrity-")
    elif scenario in {
        "environment-startup", "image-pull", "broken-entrypoint", "dependency-setup",
        "runner-failure", "artifact-collection", "provider-rate-limit",
        "provider-server-error", "provider-auth-error", "provider-network-error",
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
    elif scenario in {"verifier-crash", "verifier-result-missing", "verifier-result-malformed"}:
        value["issues"] = [scenario]
        value["verifier"] = {"outcome": "error", "result_valid": False, "reward": None}
    elif scenario == "verifier-unhealthy":
        value["verifier"] = {"outcome": "error", "result_valid": False, "reward": None}
    elif scenario != "verifier-accepted":
        raise FaultHarnessError(f"no synthetic observation generator for {scenario!r}")

    return value


def _write_artifact(directory: Path, name: str, value: JSONObject) -> Path:
    path = directory / f"{name}.json"
    try:
        path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    except OSError as error:
        raise FaultHarnessError(f"cannot write synthetic artifact {name!r}: {error}") from error
    return path


def _load_validated_policy(root: Path, policy_path: Path) -> tuple[JSONObject, str]:
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
        raise FaultHarnessError(f"cannot read validated scored-worker policy: {error}") from error
    if not isinstance(decoded, dict):
        raise FaultHarnessError("validated scored-worker policy is not a JSON object")
    policy = cast(JSONObject, decoded)
    try:
        digest = sha256(canonical_json(policy).encode("utf-8")).hexdigest()
    except (TypeError, ValueError) as error:
        raise FaultHarnessError(f"cannot canonicalize scored-worker policy: {error}") from error
    return policy, digest


def run_fault_check(root: Path, policy_path: Path) -> JSONObject:
    """Exercise every accounting reason without subprocesses or external calls."""

    _, policy_digest = _load_validated_policy(root.resolve(), policy_path)
    scenarios: list[JSONObject] = []
    outcomes: list[JSONObject] = []

    with TemporaryDirectory(prefix="rolebench-fault-check-") as temporary:
        directory = Path(temporary)
        for scenario, expected_values in _EXPECTED_OUTCOMES.items():
            expected = _expected_object(expected_values)
            diagnostics: list[str] = []
            observation_valid = False
            outcome_valid = False
            outcome: JSONObject | None = None

            try:
                observation = _observation(scenario, policy_digest)
                observation_path = _write_artifact(directory, f"{scenario}-observation", observation)
                validation = validate_artifact(root, "attempt-observation", observation_path)
                observation_valid = validation.valid
                diagnostics.extend(f"observation: {item}" for item in _diagnostics(validation))
                if observation_valid:
                    outcome = classify_attempt(observation)
                    outcome_path = _write_artifact(directory, f"{scenario}-outcome", outcome)
                    validation = validate_artifact(root, "attempt-outcome", outcome_path)
                    outcome_valid = validation.valid
                    diagnostics.extend(f"outcome: {item}" for item in _diagnostics(validation))
            except (AccountingError, ContractError, FaultHarnessError, TypeError, ValueError) as error:
                diagnostics.append(f"scenario error: {type(error).__name__}: {error}")

            actual = _actual_object(outcome)
            if actual != expected:
                diagnostics.append(
                    f"classification mismatch: expected {canonical_json(expected)}, actual {canonical_json(actual)}"
                )
            passed = observation_valid and outcome_valid and actual == expected and not diagnostics
            if outcome is not None and outcome_valid:
                outcomes.append(outcome)
            scenarios.append({
                "scenario": scenario,
                "expected": expected,
                "actual": actual,
                "observation_valid": observation_valid,
                "outcome_valid": outcome_valid,
                "passed": passed,
                "diagnostics": diagnostics,
            })

    expected_reasons = set(_EXPECTED_OUTCOMES)
    rule_reasons = set(ATTEMPT_OUTCOME_RULES)
    missing = sorted(rule_reasons - expected_reasons)
    extra = sorted(expected_reasons - rule_reasons)
    covered = sorted(expected_reasons & rule_reasons)
    coverage_valid = not missing and not extra
    if not coverage_valid:
        message = f"coverage mismatch: missing={missing!r}, extra={extra!r}"
        cast(list[JSONValue], scenarios[0]["diagnostics"]).append(message)
        scenarios[0]["passed"] = False

    try:
        quality_summary = summarize_outcomes(outcomes)
    except (AccountingError, TypeError, ValueError) as error:
        quality_summary = {}
        cast(list[JSONValue], scenarios[0]["diagnostics"]).append(
            f"summary error: {type(error).__name__}: {error}"
        )
        scenarios[0]["passed"] = False
    if quality_summary != _EXPECTED_SUMMARY:
        cast(list[JSONValue], scenarios[0]["diagnostics"]).append(
            f"summary mismatch: expected {canonical_json(_EXPECTED_SUMMARY)}, actual {canonical_json(quality_summary)}"
        )
        scenarios[0]["passed"] = False

    passed_scenarios = sum(item.get("passed") is True for item in scenarios)
    failed_scenarios = len(scenarios) - passed_scenarios
    return {
        "schema_version": "omp.worker-fault-check-report/v1",
        "passed": failed_scenarios == 0 and coverage_valid and quality_summary == _EXPECTED_SUMMARY,
        "external_calls": 0,
        "policy_digest_sha256": policy_digest,
        "scenario_count": len(scenarios),
        "expected_reason_count": len(expected_reasons),
        "covered_reason_count": len(covered),
        "passed_scenarios": passed_scenarios,
        "failed_scenarios": failed_scenarios,
        "covered_reason_codes": covered,
        "scenarios": scenarios,
        "quality_summary": quality_summary,
    }
