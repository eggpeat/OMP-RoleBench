from __future__ import annotations

import time


def check_liveness(status_monitor: object) -> dict[str, object]:
    """Health check endpoint. Requires no changes."""
    now = time.time()
    status_monitor.log_event("health.check", now)
    return {"status": "ok", "timestamp": now}
