"""Contract validation and inspection for OMP rolebench."""

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
)

__all__ = [
    "BUILTIN_ROLES",
    "ContractError",
    "Diagnostic",
    "ValidationResult",
    "canonical_digest",
    "canonical_json",
    "discover_root",
    "load_repository",
    "validate_artifact",
    "validate_repository",
]
