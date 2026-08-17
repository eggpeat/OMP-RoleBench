from __future__ import annotations

import legacy_telemetry


def process_invoice(invoice_id: str, amount: float, payment_gateway: object) -> bool:
    """Process an invoice payment."""
    legacy_telemetry.record_metric("billing.invoice_processed", 1, unit="count")
    legacy_telemetry.log_event(
        "billing.charged",
        {"invoice_id": invoice_id, "amount": amount},
        "NORMAL",
    )
    payment_gateway.record_metric("gateway.charge", amount)
    return True
