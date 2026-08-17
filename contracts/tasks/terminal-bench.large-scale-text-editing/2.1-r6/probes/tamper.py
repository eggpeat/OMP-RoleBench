#!/usr/bin/env python3
"""Tamper probe making over-broad edits to negative controls and untouched files."""

import json

SUBMISSION = {
    "schema_version": "rolebench.text-editing-submission/v1",
    "modified_files": {
        "src/auth.py": (
            "from __future__ import annotations\n\n"
            "from core.telemetry import emit_event, record_stat\n\n\n"
            "def authenticate_user(username: str, password_hash: str, audit_logger: object) -> bool:\n"
            "    \"\"\"Authenticate a user and record telemetry.\"\"\"\n"
            "    record_stat(\"auth.attempt\", count=1, tags=[\"prod\"])\n"
            "    if not username or not password_hash:\n"
            "        record_stat(\"auth.failure\", count=1, tags=[\"prod\", \"security\"])\n"
            "        emit_event(\"auth.failed\", data={\"user\": username}, level=\"high\")\n"
            "        return False\n\n"
            "    # Note: log_event in legacy system was synchronous\n"
            "    emit_event(\"auth.success\", data={\"user\": username})\n"
            "    audit_logger.emit_event(\"audit.login\", username)\n"  # Collateral edit to object method
            "    return True\n"
        ),
        "src/billing.py": (
            "from __future__ import annotations\n\n"
            "import core.telemetry as telemetry\n\n\n"
            "def process_invoice(invoice_id: str, amount: float, payment_gateway: object) -> bool:\n"
            "    \"\"\"Process an invoice payment.\"\"\"\n"
            "    telemetry.record_stat(\"billing.invoice_processed\", count=1, unit=\"count\")\n"
            "    telemetry.emit_event(\n"
            "        \"billing.charged\",\n"
            "        data={\"invoice_id\": invoice_id, \"amount\": amount},\n"
            "        level=\"normal\",\n"
            "    )\n"
            "    payment_gateway.record_metric(\"gateway.charge\", amount)\n"
            "    return True\n"
        ),
        "src/notifications.py": (
            "from __future__ import annotations\n\n"
            "from core.telemetry import emit_event\n\n\n"
            "class NotificationManager:\n"
            "    \"\"\"Manages notifications. Legacy log_event docstring.\"\"\"\n\n"
            "    def __init__(self, dispatcher: object) -> None:\n"
            "        self.dispatcher = dispatcher\n\n"
            "    def send_alert(self, user_id: str, message: str, channel: str) -> None:\n"
            "        emit_event(\n"
            "            \"notify.sent\",\n"
            "            data={\"user_id\": user_id, \"channel\": channel},\n"
            "            level=\"low\",\n"
            "        )\n"
            "        self.dispatcher.log_event(channel, message)\n"
        ),
        "src/orders.py": (
            "from __future__ import annotations\n\n"
            "from core.telemetry import emit_event, record_stat\n\n\n"
            "def record_gauge(name: str, value: float) -> None:\n"
            "    pass\n\n\n"
            "def place_order(order_id: str, items: list[str], total: float, metrics_collector: object) -> None:\n"
            "    record_stat(\"order.placed\", count=1, unit=\"count\", tags=[\"checkout\", \"v1\"])\n"
            "    emit_event(\"order.created\", data={\"order_id\": order_id, \"total\": total})\n"
            "    metrics_collector.record_metric(\"legacy.order\", 1)\n"
            "    record_gauge(\"order.total_value\", total)\n"
        ),
        "src/health.py": (
            "from __future__ import annotations\n\n"
            "import time\n\n\n"
            "def check_liveness(status_monitor: object) -> dict[str, object]:\n"
            "    \"\"\"Health check endpoint. Requires no changes.\"\"\"\n"
            "    now = time.time()\n"
            "    status_monitor.emit_event(\"health.check\", now)\n"  # Collateral edit to untouched file
            "    return {\"status\": \"ok\", \"timestamp\": now}\n"
        ),
    },
}

if __name__ == "__main__":
    print(json.dumps(SUBMISSION, sort_keys=True, separators=(",", ":")))
