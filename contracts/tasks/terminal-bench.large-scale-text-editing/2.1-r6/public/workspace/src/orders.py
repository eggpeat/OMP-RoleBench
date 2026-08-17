from __future__ import annotations

from legacy_telemetry import log_event, record_metric


def record_gauge(name: str, value: float) -> None:
    pass


def place_order(order_id: str, items: list[str], total: float, metrics_collector: object) -> None:
    record_metric("order.placed", 1, "count", ["checkout", "v1"])
    log_event("order.created", payload={"order_id": order_id, "total": total})
    metrics_collector.record_metric("legacy.order", 1)
    record_gauge("order.total_value", total)
