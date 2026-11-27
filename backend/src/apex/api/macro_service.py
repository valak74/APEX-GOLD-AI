"""Background refresh of fundamental data (FRED, CFTC) — off in tests, independent failures reported."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from apex.core.logging import get_logger
from apex.intel.macro import SyncReport, sync_macro

log = get_logger(__name__)


class MacroService:
    def __init__(self, state: Any):
        self.state = state
        self.last: SyncReport | None = None
        self.running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def sync_now(self) -> SyncReport:
        s = self.state.settings
        self.running = True
        try:
            key = s.fred_api_key.get_secret_value() if s.fred_api_key else None
            self.last = await sync_macro(self.state.sessionmaker, key, s.macro_history_start)
            return self.last
        finally:
            self.running = False

    async def _loop(self) -> None:
        await asyncio.sleep(20)  # let the API settle first
        while True:
            try:
                await self.sync_now()
            except Exception as e:  # keep the loop alive
                log.error("macro_sync_failed", error=type(e).__name__)
            await asyncio.sleep(self.state.settings.macro_sync_interval_s)
