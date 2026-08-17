from __future__ import annotations

from legacy_telemetry import log_event


class NotificationManager:
    """Manages notifications. Legacy log_event docstring."""

    def __init__(self, dispatcher: object) -> None:
        self.dispatcher = dispatcher

    def send_alert(self, user_id: str, message: str, channel: str) -> None:
        log_event(
            "notify.sent",
            payload={"user_id": user_id, "channel": channel},
            priority="LOW",
        )
        self.dispatcher.log_event(channel, message)
