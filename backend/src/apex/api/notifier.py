"""Alerts to the owner's phone (Telegram bot). Fire-and-forget: an alert failure never affects trading.

Setup (owner): talk to @BotFather to create a bot → token; send it a message, then read the chat id from
https://api.telegram.org/bot<token>/getUpdates. Put both in .env (APEX_TELEGRAM_BOT_TOKEN, APEX_TELEGRAM_CHAT_ID).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from datetime import datetime
from typing import Any

import httpx

from apex.core.logging import get_logger
from apex.domain.models import utcnow

log = get_logger(__name__)
TELEGRAM = "https://api.telegram.org"


class Notifier:
    def __init__(self, token: str | None, chat_id: str | None, http: httpx.AsyncClient | None = None) -> None:
        self.token, self.chat_id = token, chat_id
        self._http = http
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._task: asyncio.Task[None] | None = None
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None
        self.recent: deque[tuple[datetime, str]] = deque(maxlen=50)  # also shown in the dashboard

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def notify(self, text: str) -> None:
        """Queue a message (never blocks, never raises). Kept in ``recent`` even without Telegram."""
        self.recent.appendleft((utcnow(), text))
        if not self.configured:
            return
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(text[:3500])

    async def send(self, text: str) -> bool:
        client = self._http or httpx.AsyncClient(timeout=10.0)
        try:
            r = await client.post(
                f"{TELEGRAM}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            )
            r.raise_for_status()
            self.sent += 1
            return True
        except httpx.HTTPError as e:  # the token is in the URL: never log the URL or the exception text
            self.failed += 1
            self.last_error = type(e).__name__
            log.warning("alert_failed", error=self.last_error)
            return False
        finally:
            if self._http is None:
                await client.aclose()

    async def _loop(self) -> None:
        while True:
            text = await self._queue.get()
            await self.send(text)
            await asyncio.sleep(1.0)  # Telegram: stay far below rate limits

    def start(self) -> None:
        if self.configured:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "sent": self.sent,
            "failed": self.failed,
            "last_error": self.last_error,
            "recent": [{"at": t, "text": m} for t, m in list(self.recent)[:20]],
        }
