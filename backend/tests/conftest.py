"""Shared test setup: persisted trading state and the paper account must not leak between tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from apex.db.session import make_engine

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")


@pytest.fixture(autouse=True)
async def _clean_persisted_state() -> AsyncIterator[None]:
    engine = make_engine(DB_URL)
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "TRUNCATE core.trading_state, trading.paper_positions, trading.paper_orders, "
                    "trading.trades RESTART IDENTITY"
                )
            )
    except (OSError, DBAPIError):
        pass  # DB-less tests; DB tests skip or fail on their own
    finally:
        await engine.dispose()
    yield
