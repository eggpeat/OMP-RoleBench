"""Shared reason tuples for attempt outcome generation and validation."""

from __future__ import annotations


type OutcomeRule = tuple[
    str,
    tuple[str, ...],
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
]

_ALL_TERMINATIONS = (
    "completed",
    "model-deadline",
    "orchestrator-timeout",
    "resource-limit",
    "operator-cancelled",
    "signal",
    "unknown",
)
_ALL_OOM_SCOPES = ("none", "attempt", "host", "unknown")
_NON_HOST_OOM_SCOPES = ("none", "attempt", "unknown")
_SYSTEM_FAILURE_TERMINATIONS = (
    "completed", "model-deadline", "resource-limit", "signal", "unknown"
)
_VERIFIER_FAILURE_TERMINATIONS = ("completed", "model-deadline", "resource-limit")
_VERIFIER_FAILURE_OOM_SCOPES = ("none", "attempt")

ATTEMPT_OUTCOME_RULES: dict[str, OutcomeRule] = {
    "verifier-accepted": (
        "scored", ("none",), "accepted", "accepted", ("completed",), ("none",)
    ),
    "verifier-rejected": (
        "scored", ("model_task",), "rejected", "rejected", ("completed",), ("none",)
    ),
    "model-deadline": (
        "scored",
        ("timeout_model_deadline",),
        "rejected",
        "indeterminate",
        ("model-deadline",),
        ("none",),
    ),
    "attempt-resource-limit": (
        "scored",
        ("model_task",),
        "rejected",
        "indeterminate",
        ("resource-limit",),
        ("attempt",),
    ),
    "operator-cancelled": (
        "cancelled",
        ("external_cancellation",),
        "no-valid-attempt",
        "indeterminate",
        ("operator-cancelled",),
        ("none",),
    ),
    "suspected-reward-hacking": (
        "quarantined",
        ("suspected_reward_hacking",),
        "no-valid-attempt",
        "indeterminate",
        _ALL_TERMINATIONS,
        _ALL_OOM_SCOPES,
    ),
    "sandbox-violation": (
        "quarantined",
        ("policy_sandbox",),
        "no-valid-attempt",
        "indeterminate",
        _ALL_TERMINATIONS,
        _ALL_OOM_SCOPES,
    ),
    "artifact-tampering": (
        "quarantined",
        ("integrity",),
        "no-valid-attempt",
        "indeterminate",
        _ALL_TERMINATIONS,
        _ALL_OOM_SCOPES,
    ),
    "integrity-failed": (
        "quarantined",
        ("integrity",),
        "no-valid-attempt",
        "indeterminate",
        _ALL_TERMINATIONS,
        _ALL_OOM_SCOPES,
    ),
    "integrity-unknown": (
        "quarantined",
        ("integrity",),
        "no-valid-attempt",
        "indeterminate",
        _ALL_TERMINATIONS,
        _ALL_OOM_SCOPES,
    ),
    "environment-startup": (
        "retryable-invalid", ("environment_image",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "image-pull": (
        "retryable-invalid", ("environment_image",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "broken-entrypoint": (
        "retryable-invalid", ("environment_image",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "dependency-setup": (
        "retryable-invalid", ("dependency_download",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "runner-failure": (
        "retryable-invalid", ("runner_harness",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "artifact-collection": (
        "retryable-invalid", ("runner_harness",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "provider-rate-limit": (
        "retryable-invalid", ("provider_api",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "provider-server-error": (
        "retryable-invalid", ("provider_api",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "provider-auth-error": (
        "retryable-invalid", ("provider_api",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "provider-network-error": (
        "retryable-invalid", ("provider_api",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "runtime-incompatible": (
        "retryable-invalid", ("environment_image",), "no-valid-attempt",
        "indeterminate", _SYSTEM_FAILURE_TERMINATIONS, _NON_HOST_OOM_SCOPES
    ),
    "host-resource-exhaustion": (
        "retryable-invalid",
        ("runner_harness",),
        "no-valid-attempt",
        "indeterminate",
        ("resource-limit",),
        ("host",),
    ),
    "orchestrator-timeout": (
        "retryable-invalid",
        ("timeout_orchestrator",),
        "no-valid-attempt",
        "indeterminate",
        ("orchestrator-timeout",),
        ("none",),
    ),
    "verifier-crash": (
        "retryable-invalid", ("verifier",), "no-valid-attempt",
        "error", _VERIFIER_FAILURE_TERMINATIONS, _VERIFIER_FAILURE_OOM_SCOPES
    ),
    "verifier-result-missing": (
        "retryable-invalid", ("verifier",), "no-valid-attempt",
        "error", _VERIFIER_FAILURE_TERMINATIONS, _VERIFIER_FAILURE_OOM_SCOPES
    ),
    "verifier-result-malformed": (
        "retryable-invalid", ("verifier",), "no-valid-attempt",
        "error", _VERIFIER_FAILURE_TERMINATIONS, _VERIFIER_FAILURE_OOM_SCOPES
    ),
    "verifier-unhealthy": (
        "retryable-invalid", ("verifier",), "no-valid-attempt",
        "error", _VERIFIER_FAILURE_TERMINATIONS, _VERIFIER_FAILURE_OOM_SCOPES
    ),
    "incomplete-observation": (
        "retryable-invalid",
        ("runner_harness", "environment_image", "provider_api"),
        "no-valid-attempt",
        "indeterminate",
        ("completed", "model-deadline", "resource-limit", "signal", "unknown"),
        _NON_HOST_OOM_SCOPES,
    ),
}
