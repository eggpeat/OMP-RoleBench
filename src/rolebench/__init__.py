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
    task_content_sha256,
    task_execution_sha256,
    load_repository,
    validate_artifact,
    validate_repository,
    validate_value,
)

from .ledger import (
    LedgerError,
    append_artifacts,
    append_worker_report,
    verify_ledger,
)
from .task_workflow import (
    TaskAdmissionError,
    TaskWorkflowError,
    check_task_qualification,
    generate_task_qualification,
    import_omp_gym_task,
    prepare_admission_worker_manifest,
    prepare_worker_manifest,
    scan_session_candidates,
    verify_task_pack,
)

__all__ = [
    "BUILTIN_ROLES",
    "AccountingError",
    "ContractError",
    "Diagnostic",
    "LedgerError",
    "TaskAdmissionError",
    "TaskWorkflowError",
    "ValidationResult",
    "append_artifacts",
    "append_worker_report",
    "check_task_qualification",
    "classify_attempt",
    "canonical_digest",
    "canonical_json",
    "discover_root",
    "generate_task_qualification",
    "import_omp_gym_task",
    "load_repository",
    "prepare_admission_worker_manifest",
    "prepare_worker_manifest",
    "scan_session_candidates",
    "task_content_sha256",
    "task_execution_sha256",
    "summarize_outcomes",
    "validate_artifact",
    "validate_repository",
    "validate_value",
    "verify_ledger",
    "verify_task_pack",
]
