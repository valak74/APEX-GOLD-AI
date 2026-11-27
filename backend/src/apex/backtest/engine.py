"""Event-driven bar backtester that reuses the live risk engine.

Execution model (conservative by design — see docs/14-backtesting.md):
* signal at the close of bar i → entry at the OPEN of bar i+1, at ask (long) / bid (short) = mid ± spread/2,
  plus ``slippage`` price units against us;
* spread per bar = the bar's recorded average spread × ``spread_mult`` (stress tests use > 1);
* exits checked from bar i+1 on, on the closing side (bid for longs, ask for shorts):
  - a gap through the stop fills at the bar open (worse than the stop),
  - stop and target inside the same bar → the STOP is assumed hit first,
  - target fills at the target price, stop at the stop price minus slippage,
  - time exit after ``max_hold_bars`` at the bar close, and forced exit before the weekly close;
* commission charged on both sides from the symbol specification; optional swap per lot per rollover crossed;
* sizing and every risk limit come from ``apex.risk.engine.evaluate`` (same 23 checks as live).
Account currency is USD in backtests (no FX conversion noise).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np

from apex.analysis.indicators import Candles, atr
from apex.backtest.signals import Signal, StrategyParams, generate
from apex.core.fx import FxConverter
from apex.core.money import Money
from apex.domain.enums import Side, TradingMode
from apex.domain.models import AccountInfo, Quote, SymbolSpec
from apex.market.sessions import NY, is_market_open, trading_day_start
from apex.risk.config import RiskConfig
from apex.risk.engine import Proposal, Snapshot, evaluate
from apex.risk.ledger import TradeRecord, compute_stats


@dataclass(frozen=True)
class CostModel:
    spread_mult: float = 1.0
    slippage: float = 0.05  # price units, each side
    swap_per_lot_night: float = (
        0.0  # USD per lot per rollover crossed (0 = not modelled — flagged in reports)
    )
    commission_mult: float = 1.0


@dataclass
class BTTrade:
    signal_t: datetime
    entry_t: datetime
    exit_t: datetime
    direction: str
    setup: str
    regime: str
    quality: int
    volume: float
    entry: float
    stop: float
    target: float
    exit: float
    reason: str
    gross: float
    costs: float
    net: float
    risk_amount: float
    r: float
    mfe_r: float
    mae_r: float
    bars_held: int


@dataclass
class BacktestResult:
    params: dict[str, Any]
    costs: dict[str, Any]
    start: datetime | None
    end: datetime | None
    initial_balance: float
    trades: list[BTTrade] = field(default_factory=list)
    equity: list[tuple[datetime, float]] = field(default_factory=list)
    signals: int = 0
    rejected: dict[str, int] = field(default_factory=dict)


def _bar_spread(spreads: np.ndarray, i: int, mult: float) -> float:
    s = float(spreads[i]) if i < len(spreads) and not np.isnan(spreads[i]) else 0.3
    return max(s, 0.01) * mult


def _weekly_close_next(t: datetime, nxt: datetime | None) -> bool:
    return nxt is None or (not is_market_open(nxt) and t.astimezone(NY).weekday() == 4)


def _rollovers_between(a: datetime, b: datetime) -> int:
    """Daily rollovers (17:00 New York) crossed between two instants; DST-safe."""
    return round((trading_day_start(b) - trading_day_start(a)).total_seconds() / 86400)


def run(
    c: Candles,
    spreads: np.ndarray,
    spec: SymbolSpec,
    params: StrategyParams,
    cfg: RiskConfig,
    costs: CostModel | None = None,
    initial_balance: float = 10_000.0,
    news: list[tuple[datetime, datetime]] | None = None,
    signals: list[Signal] | None = None,
) -> BacktestResult:
    costs = costs or CostModel()
    sigs = signals if signals is not None else generate(c, params)
    a = atr(c)
    res = BacktestResult(
        params=params.__dict__ | {"sessions": list(params.sessions)},
        costs=costs.__dict__,
        start=c.t[0] if len(c) else None,
        end=c.t[-1] if len(c) else None,
        initial_balance=initial_balance,
        signals=len(sigs),
    )
    balance = initial_balance
    ledger: list[TradeRecord] = []
    by_idx: dict[int, Signal] = {}
    for s in sigs:
        by_idx.setdefault(s.idx, s)
    news = news or []
    spec_c = spec.model_copy(
        update={
            "commission_rate": spec.commission_rate * Decimal(str(costs.commission_mult)),
            "min_commission": spec.min_commission * Decimal(str(costs.commission_mult)),
        }
    )
    i, n = 0, len(c)
    res.equity.append((c.t[0], balance)) if n else None
    while i < n - 1:
        sig = by_idx.get(i)
        if sig is None:
            i += 1
            continue
        j = i + 1  # entry bar
        t = c.t[j]
        sp = _bar_spread(spreads, j, costs.spread_mult)
        mid_open = float(c.o[j])
        bid, ask = mid_open - sp / 2, mid_open + sp / 2
        side = Side.BUY if sig.direction == "bullish" else Side.SELL
        entry = (ask + costs.slippage) if side is Side.BUY else (bid - costs.slippage)
        acc = AccountInfo(
            mode=TradingMode.SIMULATION,
            account_id="bt",
            currency="USD",
            balance=Money.of(f"{balance:.2f}", "USD"),
            equity=Money.of(f"{balance:.2f}", "USD"),
            margin_used=Money.of(0, "USD"),
            free_margin=Money.of(f"{balance:.2f}", "USD"),
            leverage=20,
            margin_level_pct=None,
        )
        lock = next((f"annonce {s:%d/%m %H:%M}" for s, e in news if s <= t <= e), None)
        snap = Snapshot(
            now=t,
            mode=TradingMode.SIMULATION,
            trading_enabled=True,
            kill_switch=False,
            account=acc,
            positions=[],
            quote=Quote(symbol="XAUUSD", bid=Decimal(f"{bid:.2f}"), ask=Decimal(f"{ask:.2f}"), ts=t),
            spec=spec_c,
            fx=FxConverter(),
            ledger=compute_stats(ledger, Decimal(str(initial_balance)), t),
            data_quality_ok=True,
            data_quality_issues=[],
            news_lock_reason=lock,
            atr=None if np.isnan(a[i]) else Decimal(f"{a[i]:.4f}"),
            regime_multiplier=Decimal(str(sig.regime_multiplier))
            if sig.regime_multiplier > 0
            else Decimal(1),
        )
        d = evaluate(
            Proposal(
                f"bt-{i}",
                "XAUUSD",
                side,
                Decimal(f"{entry:.2f}"),
                Decimal(f"{sig.stop:.2f}"),
                Decimal(f"{sig.target:.2f}"),
            ),
            snap,
            cfg,
        )
        if not d.approved or d.order is None or d.sizing is None or d.sizing.risk_amount is None:
            for code in d.rejections:
                res.rejected[code] = res.rejected.get(code, 0) + 1
            i += 1
            continue
        vol = float(d.order.volume)
        risk_amount = float(d.sizing.risk_amount.amount)
        sgn = 1 if side is Side.BUY else -1
        stop, target = sig.stop, sig.target
        unit_risk = abs(entry - stop)
        exit_px, reason, k = None, "", j
        mfe = mae = 0.0
        for k in range(j, n):
            spk = _bar_spread(spreads, k, costs.spread_mult)
            close_side = -sgn * spk / 2  # longs exit on bid (mid - s/2), shorts on ask (mid + s/2)
            o, hi, lo, cl = (
                float(c.o[k]) + close_side,
                float(c.h[k]) + close_side,
                float(c.l[k]) + close_side,
                float(c.c[k]) + close_side,
            )
            fav = (hi - entry) * sgn if sgn > 0 else (entry - lo)
            adv = (entry - lo) if sgn > 0 else (hi - entry)
            mfe, mae = max(mfe, fav), max(mae, adv)
            if k > j and ((o - stop) * sgn <= 0):  # gap through the stop
                exit_px, reason = o - sgn * costs.slippage, "stop_gap"
                break
            hit_stop = (lo <= stop) if sgn > 0 else (hi >= stop)
            hit_tgt = (hi >= target) if sgn > 0 else (lo <= target)
            if hit_stop:  # stop first when both are inside the same bar
                exit_px, reason = stop - sgn * costs.slippage, "stop"
                break
            if hit_tgt:
                exit_px, reason = target, "target"
                break
            nxt = c.t[k + 1] if k + 1 < n else None
            if k - j + 1 >= params.max_hold_bars:
                exit_px, reason = cl - sgn * costs.slippage, "time"
                break
            if _weekly_close_next(c.t[k], nxt):
                exit_px, reason = cl - sgn * costs.slippage, "weekly_close"
                break
        if exit_px is None:
            exit_px, reason = float(c.c[-1]) - sgn * _bar_spread(spreads, n - 1, 1) / 2, "end_of_data"
        contract = float(spec.contract_size)
        gross = (exit_px - entry) * sgn * vol * contract
        comm = float(spec_c.commission_for(Decimal(str(vol)), Decimal(f"{entry:.2f}"))) + float(
            spec_c.commission_for(Decimal(str(vol)), Decimal(f"{exit_px:.2f}"))
        )
        swap = costs.swap_per_lot_night * vol * _rollovers_between(t, c.t[k])
        net = gross - comm - swap
        balance += net
        ledger.append(TradeRecord(c.t[k], Decimal(f"{net:.2f}")))
        res.equity.append((c.t[k], balance))
        res.trades.append(
            BTTrade(
                signal_t=sig.t,
                entry_t=t,
                exit_t=c.t[k],
                direction=sig.direction,
                setup=sig.setup,
                regime=sig.regime,
                quality=sig.quality,
                volume=vol,
                entry=round(entry, 2),
                stop=round(stop, 2),
                target=round(target, 2),
                exit=round(exit_px, 2),
                reason=reason,
                gross=round(gross, 2),
                costs=round(comm + swap, 2),
                net=round(net, 2),
                risk_amount=round(risk_amount, 2),
                r=round(net / risk_amount, 3) if risk_amount else 0.0,
                mfe_r=round(mfe / unit_risk, 2) if unit_risk else 0.0,
                mae_r=round(mae / unit_risk, 2) if unit_risk else 0.0,
                bars_held=k - j + 1,
            )
        )
        i = k + 1  # one position at a time: next signal after the exit
    return res


__all__ = ["BTTrade", "BacktestResult", "CostModel", "run"]
