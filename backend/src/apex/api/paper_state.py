"""Continuity of a PAPER run across restarts.

* Trading switches: the kill switch ALWAYS survives a restart. The trading switch and the autopilot are restored
  only in PAPER mode (simulated money): a LIVE process always starts disabled and needs a fresh human activation.
* Simulated account: open positions are saved on every change and re-created at startup; the balance is rebuilt
  from the trade journal (initial balance + closed net P&L − commissions already paid on open positions).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from apex.broker.sim import SimBroker
from apex.core.logging import get_logger
from apex.db.audit import append_audit
from apex.db.models import PaperPositionRow, TradeRow, TradingStateRow
from apex.domain.enums import Side, TradingMode
from apex.domain.models import ClosedTrade, Position, utcnow

log = get_logger(__name__)


async def save_trading_state(state: Any) -> None:
    t = state.trading
    values = {
        "id": 1,
        "trading_enabled": t.trading_enabled,
        "kill_switch_engaged": t.kill_switch_engaged,
        "kill_switch_reason": t.kill_switch_reason,
        "kill_switch_at": t.kill_switch_at,
        "kill_switch_by": t.kill_switch_by,
        "autopilot_enabled": t.autopilot_enabled,
        "autopilot_config": t.autopilot_config,
        "updated_at": utcnow(),
    }
    stmt = insert(TradingStateRow).values(**values)
    async with state.sessionmaker() as s:
        await s.execute(
            stmt.on_conflict_do_update(
                index_elements=["id"], set_={k: v for k, v in values.items() if k != "id"}
            )
        )
        await s.commit()


async def restore_trading_state(state: Any) -> None:
    async with state.sessionmaker() as s:
        row = await s.get(TradingStateRow, 1)
        if row is None:
            return
        t = state.trading
        t.kill_switch_engaged = row.kill_switch_engaged
        t.kill_switch_reason, t.kill_switch_at, t.kill_switch_by = (
            row.kill_switch_reason,
            row.kill_switch_at,
            row.kill_switch_by,
        )
        t.autopilot_config = dict(row.autopilot_config or {})
        paper = state.broker.mode is TradingMode.PAPER
        t.trading_enabled = paper and row.trading_enabled and not row.kill_switch_engaged
        t.autopilot_enabled = paper and row.autopilot_enabled and not row.kill_switch_engaged
        await append_audit(
            s,
            "system",
            "trading_state_restored",
            details={
                "mode": state.broker.mode.value,
                "kill_switch": t.kill_switch_engaged,
                "trading_enabled": t.trading_enabled,
                "autopilot": t.autopilot_enabled,
            },
        )
        await s.commit()


class PaperAccountStore:
    """Saves simulated positions on every change; restores account and positions at startup."""

    def __init__(self, state: Any) -> None:
        self.state = state
        self._writes: set[asyncio.Task[None]] = set()
        b = state.broker
        self.enabled = isinstance(b, SimBroker) and b.mode is TradingMode.PAPER
        if self.enabled:
            b.position_listeners.append(self._on_position)
            b.trade_listeners.append(self._on_trade)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    def _on_position(
        self, event: str, p: Position, coid: str | None, comm: Decimal, comm_acc: Decimal
    ) -> None:
        self._spawn(self._save(event, p, coid, comm, comm_acc))

    def _on_trade(self, t: ClosedTrade) -> None:
        self._spawn(self._closed(t))

    async def _save(
        self, event: str, p: Position, coid: str | None, comm: Decimal, comm_acc: Decimal
    ) -> None:
        async with self.state.sessionmaker() as s:
            if event == "opened":
                s.add(
                    PaperPositionRow(
                        position_id=str(p.position_id),
                        account_id=self.state.broker.account_id,
                        client_order_id=coid,
                        symbol=p.symbol,
                        side=p.side.value,
                        volume=p.volume,
                        open_price=p.open_price,
                        stop_loss=p.stop_loss,
                        take_profit=p.take_profit,
                        opened_at=p.opened_at,
                        open_commission=comm,
                        open_commission_acc=comm_acc,
                    )
                )
            else:
                row = await s.get(PaperPositionRow, str(p.position_id))
                if row is not None:
                    row.stop_loss, row.take_profit = p.stop_loss, p.take_profit
            await s.commit()

    async def _closed(self, t: ClosedTrade) -> None:
        pid = str(t.position_id)
        async with self.state.sessionmaker() as s:
            row = await s.get(PaperPositionRow, pid)
            if row is None:
                return
            if t.volume >= row.volume:
                await s.execute(delete(PaperPositionRow).where(PaperPositionRow.position_id == pid))
            else:  # partial close: the remaining part keeps its share of the open commission
                share = (row.volume - t.volume) / row.volume
                row.volume -= t.volume
                row.open_commission *= share
                row.open_commission_acc *= share
            await s.commit()

    async def flush(self) -> None:
        if self._writes:
            await asyncio.gather(*list(self._writes), return_exceptions=True)

    async def restore(self) -> dict[str, Any]:
        if not self.enabled:
            return {}
        b: SimBroker = self.state.broker
        async with self.state.sessionmaker() as s:
            closed = (
                await s.execute(
                    select(func.coalesce(func.sum(TradeRow.net_pnl), 0)).where(
                        TradeRow.mode == b.mode.value, TradeRow.account_id == b.account_id
                    )
                )
            ).scalar_one()
            rows = (
                (await s.execute(select(PaperPositionRow).where(PaperPositionRow.account_id == b.account_id)))
                .scalars()
                .all()
            )
        initial = Decimal(self.state.settings.paper_initial_balance)
        paid = sum((r.open_commission_acc for r in rows), Decimal(0))
        b.restore_balance(initial + Decimal(closed) - paid)
        for r in rows:
            b.restore_position(
                UUID(r.position_id),
                r.symbol,
                Side(r.side),
                r.volume,
                r.open_price,
                r.stop_loss,
                r.take_profit,
                r.opened_at,
                r.open_commission,
                r.client_order_id,
            )
        out = {"balance": str(initial + Decimal(closed) - paid), "positions": len(rows)}
        log.info("paper_account_restored", **out)
        return out
