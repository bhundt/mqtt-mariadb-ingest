from __future__ import annotations

import logging

import requests


class NtfyNotifier:
    def __init__(self, base_url: str, topic: str, enabled: bool):
        self.base_url = base_url.rstrip("/")
        self.topic = topic
        self.enabled = enabled
        self.logger = logging.getLogger(__name__)

    def send(self, message: str, title: str = "", is_warning: bool = False) -> None:
        if not self.enabled:
            self.logger.info("Notification suppressed: %s", message)
            return
        if not self.topic:
            self.logger.warning("Notification requested but NTFY_TOPIC is empty: %s", message)
            return

        response = requests.post(
            f"{self.base_url}/{self.topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Tags": "warning" if is_warning else "",
            },
            timeout=10,
        )
        response.raise_for_status()

