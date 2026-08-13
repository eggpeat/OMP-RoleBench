"""Contract validation and inspection for OMP rolebench."""

from .accounting import (
    AccountingError,
    classify_attempt,
    summarize_outcomes,
)

from .contracts import (
    BUILTIN_ROLES,
    ContractError,
    Diagnostic,
    ValidationResult,
    canonical_digest,
    canonical_json,
    discover_root,
    load_repository,
    validate_artifact,
    validate_repository,
    validate_value,
)

__all__ = [
    "BUILTIN_ROLES",
    "AccountingError",
    "ContractError",
    "Diagnostic",
    "ValidationResult",
    "classify_attempt",
    "canonical_digest",
    "canonical_json",
    "discover_root",
    "load_repository",
    "validate_artifact",
    "validate_repository",
    "validate_value",
    "summarize_outcomes",
]
