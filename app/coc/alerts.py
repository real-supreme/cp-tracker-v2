# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Email alerts, rate-limited by a single in-process cooldown.

Two alert types:
  - `rate_halved`: fired once when auto-halve triggers for the first time in
    a cooldown window.
  - `rate_recovered`: fired once when the halved state clears.
"""
import asyncio
import logging
import smtplib
import time
from email.message import EmailMessage

from app.config import SmtpConfig

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self, smtp: SmtpConfig | None, cooldown_seconds: int) -> None:
        self._smtp = smtp
        self._cooldown = cooldown_seconds
        self._last_sent: dict[str, float] = {}
        self._halved = False

    def _should_send(self, kind: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(kind, 0.0)
        if now - last >= self._cooldown:
            self._last_sent[kind] = now
            return True
        return False

    async def _send(self, subject: str, body: str) -> None:
        if self._smtp is None:
            logger.warning("alert (no smtp): %s | %s", subject, body)
            return
        msg = EmailMessage()
        msg["Subject"] = f"[clash-panda] {subject}"
        msg["From"] = self._smtp.user or "clash-panda@localhost"
        msg["To"] = self._smtp.alert_email
        msg.set_content(body)

        def _do_send() -> None:
            try:
                with smtplib.SMTP(self._smtp.host, self._smtp.port, timeout=10) as s:
                    s.starttls()
                    if self._smtp.user:
                        s.login(self._smtp.user, self._smtp.password)
                    s.send_message(msg)
            except Exception as e:  # noqa: BLE001 - alert failures must not crash
                logger.error("alert email failed: %s", e)

        await asyncio.to_thread(_do_send)

    async def on_rate_halved(self, key_name: str, reason: str) -> None:
        if not self._halved:
            self._halved = True
        if self._should_send("rate_halved"):
            await self._send(
                "429 received — API key rate halved",
                f"Key: {key_name}\nReason: {reason}\n\n"
                f"The tracker halved the effective rate for this key. "
                f"You'll receive one 'recovered' email when the rate returns to normal.",
            )

    async def on_rate_recovered(self) -> None:
        if not self._halved:
            return
        self._halved = False
        if self._should_send("rate_recovered"):
            await self._send(
                "API key rate recovered",
                "No 429s observed for the recovery window. Effective rate restored.",
            )