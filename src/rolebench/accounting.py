"""Deterministic classification and scoring for benchmark attempts."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256

from .accounting_rules import ATTEMPT_OUTCOME_RULES
from .contracts import JSONObject, JSONValue, canonical_json


class AccountingError(ValueError):
    """An attempt observation or outcome cannot be accounted for safely."""


_QUARANTINE_ISSUES: tuple[tuple[str, str, str], ...] = (
    ("suspected-reward-hacking", "suspected_reward_hacking", "suspected-reward-hacking"),
    ("sandbox-violation", "policy_sandbox", "sandbox-violation"),
    ("artifact-tampering", "integrity", "artifact-tampering"),
)

_INVALID_ISSUES: tuple[tuple[str, str, str, str], ...] = (
    ("environment-startup", "environment_image", "environment-startup", "indeterminate"),
    ("image-pull", "environment_image", "image-pull", "indeterminate"),
    ("broken-entrypoint", "environment_image", "broken-entrypoint", "indeterminate"),
    ("dependency-setup", "dependency_download", "dependency-setup", "indeterminate"),
    ("runner-failure", "runner_harness", "runner-failure", "indeterminate"),
    ("artifact-collection", "runner_harness", "artifact-collection", "indeterminate"),
    ("provider-rate-limit", "provider_api", "provider-rate-limit", "indeterminate"),
    ("provider-server-error", "provider_api", "provider-server-error", "indeterminate"),
    ("provider-auth-error", "provider_api", "provider-auth-error", "indeterminate"),
    ("provider-network-error", "provider_api", "provider-network-error", "indeterminate"),
    ("runtime-incompatible", "environment_image", "runtime-incompatible", "indeterminate"),
    ("verifier-crash", "verifier", "verifier-crash", "error"),
    ("verifier-result-missing", "verifier", "verifier-result-missing", "error"),
    ("verifier-result-malformed", "verifier", "verifier-result-malformed", "error"),
)

_KNOWN_ISSUES = frozenset(
    issue
    for issue, _, _ in _QUARANTINE_ISSUES
) | frozenset(
    issue
    for issue, _, _, _ in _INVALID_ISSUES
)


def _object(value: JSONValue, name: str) -> JSONObject:
    if not isinstance(value, dict):
        raise AccountingError(f"{name} must be an object")
    return value


def _string(value: JSONValue, name: str) -> str:
    if not isinstance(value, str):
        raise AccountingError(f"{name} must be a string")
    return value


def _outcome(
    observation: JSONObject,
    *,
    model_outcome: str,
    verifier_outcome: str,
    failure_domain: str,
    disposition: str,
    reason_code: str,
) -> JSONObject:
    attempt = _object(observation.get("attempt"), "attempt")
    termination = _object(observation.get("termination"), "termination")
    digests = _object(observation.get("digests"), "digests")
    scored = disposition == "scored"
    return {
        "schema_version": "omp.attempt-outcome/v1",
        "observation_id": _string(observation.get("observation_id"), "observation_id"),
        "observation_digest_sha256": sha256(
            canonical_json(observation).encode("utf-8")
        ).hexdigest(),
        "attempt": dict(attempt),
        "stage": _string(observation.get("stage"), "stage"),
        "model_outcome": model_outcome,
        "verifier_outcome": verifier_outcome,
        "failure_domain": failure_domain,
        "disposition": disposition,
        "valid_attempt": scored,
        "counts_toward_quality": scored,
        "reason_code": reason_code,
        "termination": dict(termination),
        "digests": dict(digests),
    }


def _invalid(
    observation: JSONObject,
    verifier_outcome: str,
    failure_domain: str,
    reason_code: str,
) -> JSONObject:
    return _outcome(
        observation,
        model_outcome="no-valid-attempt",
        verifier_outcome=verifier_outcome,
        failure_domain=failure_domain,
        disposition="retryable-invalid",
        reason_code=reason_code,
    )


def _model_lifecycle_complete(
    lifecycle: JSONObject,
    provider: JSONObject,
    digests: JSONObject,
) -> bool:
    return (
        lifecycle.get("environment_started") is True
        and lifecycle.get("agent_started") is True
        and lifecycle.get("agent_finished") is True
        and provider.get("request_started") is True
        and isinstance(digests.get("trajectory"), str)
    )


def _verifier_lifecycle_complete(
    lifecycle: JSONObject,
    provider: JSONObject,
    digests: JSONObject,
) -> bool:
    return (
        _model_lifecycle_complete(lifecycle, provider, digests)
        and lifecycle.get("artifact_frozen") is True
        and lifecycle.get("verifier_started") is True
        and lifecycle.get("verifier_finished") is True
        and isinstance(digests.get("artifact"), str)
    )


def classify_attempt(observation: JSONObject) -> JSONObject:
    """Classify one observation defensively without clocks, I/O, or mutable state."""

    if observation.get("schema_version") != "omp.attempt-observation/v1":
        raise AccountingError("unsupported attempt observation schema_version")

    lifecycle = _object(observation.get("lifecycle"), "lifecycle")
    readiness = _object(observation.get("readiness"), "readiness")
    provider = _object(observation.get("provider"), "provider")
    termination = _object(observation.get("termination"), "termination")
    verifier = _object(observation.get("verifier"), "verifier")
    integrity = _object(observation.get("integrity"), "integrity")
    digests = _object(observation.get("digests"), "digests")
    raw_issues = observation.get("issues")
    if not isinstance(raw_issues, list) or not all(
        isinstance(item, str) for item in raw_issues
    ):
        raise AccountingError("issues must be an array of strings")
    issues = set(raw_issues)
    if len(issues) != len(raw_issues):
        raise AccountingError("issues must not contain duplicates")
    unknown_issues = issues - _KNOWN_ISSUES
    if unknown_issues:
        raise AccountingError(f"unknown attempt issues: {sorted(unknown_issues)!r}")

    termination_kind = termination.get("kind")
    oom_scope = termination.get("oom_scope")
    if termination_kind == "resource-limit":
        if oom_scope not in {"attempt", "host", "unknown"}:
            raise AccountingError("resource-limit termination has invalid OOM scope")
    elif oom_scope != "none":
        raise AccountingError("only resource-limit termination may have an OOM scope")

    for issue, domain, reason in _QUARANTINE_ISSUES:
        if issue in issues:
            return _outcome(
                observation,
                model_outcome="no-valid-attempt",
                verifier_outcome="indeterminate",
                failure_domain=domain,
                disposition="quarantined",
                reason_code=reason,
            )
    integrity_state = integrity.get("state")
    if integrity_state != "verified":
        return _outcome(
            observation,
            model_outcome="no-valid-attempt",
            verifier_outcome="indeterminate",
            failure_domain="integrity",
            disposition="quarantined",
            reason_code=(
                "integrity-failed"
                if integrity_state == "failed"
                else "integrity-unknown"
            ),
        )

    if termination_kind == "operator-cancelled":
        return _outcome(
            observation,
            model_outcome="no-valid-attempt",
            verifier_outcome="indeterminate",
            failure_domain="external_cancellation",
            disposition="cancelled",
            reason_code="operator-cancelled",
        )
    if termination_kind == "resource-limit" and oom_scope == "host":
        return _invalid(
            observation,
            "indeterminate",
            "runner_harness",
            "host-resource-exhaustion",
        )
    if termination_kind == "orchestrator-timeout":
        return _invalid(
            observation,
            "indeterminate",
            "timeout_orchestrator",
            "orchestrator-timeout",
        )

    for issue, domain, reason, issue_verifier_outcome in _INVALID_ISSUES:
        if issue_verifier_outcome != "error" and issue in issues:
            return _invalid(
                observation,
                issue_verifier_outcome,
                domain,
                reason,
            )

    if termination_kind in {"signal", "unknown"} or (
        termination_kind == "resource-limit" and oom_scope == "unknown"
    ):
        return _invalid(
            observation,
            "indeterminate",
            "runner_harness",
            "incomplete-observation",
        )

    for issue, domain, reason, issue_verifier_outcome in _INVALID_ISSUES:
        if issue_verifier_outcome == "error" and issue in issues:
            return _invalid(
                observation,
                issue_verifier_outcome,
                domain,
                reason,
            )


    if readiness.get("environment") != "ready":
        return _invalid(
            observation,
            "indeterminate",
            "environment_image",
            "incomplete-observation",
        )
    if readiness.get("runner") != "healthy":
        return _invalid(
            observation,
            "indeterminate",
            "runner_harness",
            "runner-failure",
        )
    if readiness.get("provider") != "available":
        return _invalid(
            observation,
            "indeterminate",
            "provider_api",
            "incomplete-observation",
        )

    verifier_result = verifier.get("outcome")
    if verifier_result in {"accepted", "rejected"} and termination_kind != "completed":
        raise AccountingError(
            "a decisive verifier result requires completed termination"
        )
    if verifier_result == "error":
        return _invalid(
            observation,
            "error",
            "verifier",
            "verifier-unhealthy",
        )


    if termination_kind in {"model-deadline", "resource-limit"}:
        if provider.get("request_started") is not True:
            return _invalid(
                observation,
                "indeterminate",
                "provider_api",
                "incomplete-observation",
            )
        if not _model_lifecycle_complete(lifecycle, provider, digests):
            return _invalid(
                observation,
                "indeterminate",
                "runner_harness",
                "incomplete-observation",
            )
        if termination_kind == "model-deadline":
            return _outcome(
                observation,
                model_outcome="rejected",
                verifier_outcome="indeterminate",
                failure_domain="timeout_model_deadline",
                disposition="scored",
                reason_code="model-deadline",
            )
        if oom_scope == "attempt":
            return _outcome(
                observation,
                model_outcome="rejected",
                verifier_outcome="indeterminate",
                failure_domain="model_task",
                disposition="scored",
                reason_code="attempt-resource-limit",
            )

    if verifier_result in {"accepted", "rejected"}:
        reward = verifier.get("reward")
        reward_is_number = isinstance(reward, (int, float)) and not isinstance(
            reward, bool
        )
        valid_reward = (
            verifier.get("result_valid") is True
            and reward_is_number
            and (
                reward == 1
                if verifier_result == "accepted"
                else 0 <= reward < 1
            )
        )
        if not valid_reward:
            return _invalid(
                observation,
                "error",
                "verifier",
                "verifier-result-malformed",
            )
        if not _verifier_lifecycle_complete(lifecycle, provider, digests):
            return _invalid(
                observation,
                "indeterminate",
                "runner_harness",
                "incomplete-observation",
            )
        accepted = verifier_result == "accepted"
        return _outcome(
            observation,
            model_outcome=("accepted" if accepted else "rejected"),
            verifier_outcome=verifier_result,
            failure_domain=("none" if accepted else "model_task"),
            disposition="scored",
            reason_code=("verifier-accepted" if accepted else "verifier-rejected"),
        )

    return _invalid(
        observation,
        "error",
        "verifier",
        "verifier-result-missing",
    )




def _validate_outcome_tuple(outcome: JSONObject) -> None:
    reason_code = outcome.get("reason_code")
    if not isinstance(reason_code, str) or reason_code not in ATTEMPT_OUTCOME_RULES:
        raise AccountingError("unknown attempt outcome reason_code")
    (
        expected_disposition,
        allowed_domains,
        expected_model_outcome,
        expected_verifier_outcome,
        allowed_terminations,
        allowed_oom_scopes,
    ) = ATTEMPT_OUTCOME_RULES[reason_code]
    termination = _object(outcome.get("termination"), "termination")
    values_match = (
        outcome.get("disposition") == expected_disposition
        and outcome.get("failure_domain") in allowed_domains
        and outcome.get("model_outcome") == expected_model_outcome
        and outcome.get("verifier_outcome") == expected_verifier_outcome
        and termination.get("kind") in allowed_terminations
        and termination.get("oom_scope") in allowed_oom_scopes
    )
    if not values_match:
        raise AccountingError(
            f"attempt outcome fields conflict with reason_code {reason_code!r}"
        )

    termination_kind = termination.get("kind")
    oom_scope = termination.get("oom_scope")
    if termination_kind == "resource-limit":
        if oom_scope not in {"attempt", "host", "unknown"}:
            raise AccountingError("resource-limit outcome has invalid OOM scope")
    elif oom_scope != "none":
        raise AccountingError("only resource-limit outcomes may have an OOM scope")

    scored = expected_disposition == "scored"
    if outcome.get("valid_attempt") is not scored:
        raise AccountingError("valid_attempt conflicts with outcome disposition")
    if outcome.get("counts_toward_quality") is not scored:
        raise AccountingError(
            "counts_toward_quality conflicts with outcome disposition"
        )


def summarize_outcomes(outcomes: Sequence[JSONObject]) -> JSONObject:
    """Summarize quality separately from attempts that could not be scored."""

    accepted = 0
    rejected = 0
    retryable_invalid = 0
    quarantined = 0
    cancelled = 0

    for outcome in outcomes:
        if outcome.get("schema_version") != "omp.attempt-outcome/v1":
            raise AccountingError("unsupported attempt outcome schema_version")
        _validate_outcome_tuple(outcome)
        disposition = outcome.get("disposition")
        model_outcome = outcome.get("model_outcome")
        if model_outcome == "accepted":
            accepted += 1
        elif model_outcome == "rejected":
            rejected += 1
        elif disposition == "retryable-invalid":
            retryable_invalid += 1
        elif disposition == "quarantined":
            quarantined += 1
        elif disposition == "cancelled":
            cancelled += 1

    scored = accepted + rejected
    return {
        "total_attempts": len(outcomes),
        "scored_attempts": scored,
        "accepted": accepted,
        "rejected": rejected,
        "quality_score": accepted / scored if scored else None,
        "not_scored": {
            "retryable_invalid": retryable_invalid,
            "quarantined": quarantined,
            "cancelled": cancelled,
        },
    }
