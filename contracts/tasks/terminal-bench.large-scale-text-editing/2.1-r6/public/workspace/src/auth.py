from __future__ import annotations

from legacy_telemetry import log_event, record_metric


def authenticate_user(username: str, password_hash: str, audit_logger: object) -> bool:
    """Authenticate a user and record telemetry."""
    record_metric("auth.attempt", 1, tags=["prod"])
    if not username or not password_hash:
        record_metric("auth.failure", 1, tags=["prod", "security"])
        log_event("auth.failed", payload={"user": username}, priority="HIGH")
        return False

    # Note: log_event in legacy system was synchronous
    log_event("auth.success", payload={"user": username})
    audit_logger.log_event("audit.login", username)
    return True
