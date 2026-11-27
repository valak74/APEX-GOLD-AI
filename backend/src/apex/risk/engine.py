"""Deterministic risk engine — the final authority on whether a trade may be placed.

``evaluate(proposal, snapshot, config)`` is a pure function: identical inputs give identical decisions,
nothing
is mutated, and every check is recorded (passed or failed, with its value and limit) so the decision is fully
auditable. It is fail-closed: missing data or any internal error is a rejection.

The engine can only *reduce* risk. Regime suggestions (``risk_multiplier`` ≤ 1) and drawdown protection lower
the per-trade risk; nothing can raise it above ``config.risk_per_trade_pct`` (itself capped at 2 % in code).
Limits come from the versioned, admin-only risk configuration; no AI component can write them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from apex.core.fx import FxConverter
from apex.domain.enums import Side, TradingMode
from apex.domain.models import AccountInfo, OrderRequest, Position, Quote, SymbolSpec
from apex.market.sessions import NY, ROLLOVER, is_market_open
from apex.risk.config import RiskConfig
from apex.risk.ledger import LedgerStats
from apex.risk.sizing import SizingInput, SizingResult, compute_position_size


@dataclass(frozen=True)
class Proposal:
    """What the decision layer wants to do. Built from an opportunity; contains no size."""

    proposal_id: str  # stable (e.g. opportunity id): one order per idea, ever
    symbol: str
    side: Side
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    source: str = "opportunity"


@dataclass(frozen=True)
class Snapshot:
    """Everything the engine needs, captured at one instant."""

    now: datetime
    mode: TradingMode
    trading_enabled: bool
    kill_switch: bool
    account: AccountInfo
    positions: list[Position]
    quote: Quote | None
    spec: SymbolSpec
    fx: FxConverter
    ledger: LedgerStats
    data_quality_ok: bool
    data_quality_issues: list[str]
    news_lock_reason: str | None
    atr: Decimal | None
    regime_multiplier: Decimal = Decimal(1)
    already_submitted: bool = False


@dataclass(frozen=True)
class Check:
    code: str
    label: str  # French, shown in the dashboard
    passed: bool
    value: str = ""
    limit: str = ""
    severity: Literal["block", "critical"] = "block"


@dataclass
class RiskDecision:
    proposal_id: str
    approved: bool
    decided_at: datetime
    config_hash: str
    checks: list[Check]
    rejections: list[str]
    critical: bool  # a check that should halt all trading (e.g. max drawdown)
    risk_pct_base: Decimal
    risk_pct_effective: Decimal
    reductions: list[str] = field(default_factory=list)
    sizing: SizingResult | None = None
    order: OrderRequest | None = None
    net_risk_reward: Decimal | None = None


@dataclass(frozen=True)
class AccountMetrics:
    daily_loss: Decimal  # realised today + open P&L, as a positive loss amount
    daily_cap: Decimal
    weekly_loss: Decimal
    weekly_cap: Decimal
    drawdown: Decimal  # fraction from the realised-balance peak to current equity
    opened_today: int

    @property
    def remaining_daily(self) -> Decimal:
        return max(Decimal(0), self.daily_cap - self.daily_loss)


def account_metrics(s: Snapshot, cfg: RiskConfig) -> AccountMetrics:
    """Single source for loss/drawdown figures (used by decisions, the status page and the guard)."""
    eq, bal, led = s.account.equity.amount, s.account.balance.amount, s.ledger
    open_pnl = eq - bal
    dd = (led.peak_balance - eq) / led.peak_balance if led.peak_balance > 0 else Decimal(1)
    return AccountMetrics(
        daily_loss=-min(Decimal(0), led.realized_today + open_pnl),
        daily_cap=cfg.max_daily_loss_pct * led.day_start_balance,
        weekly_loss=-min(Decimal(0), led.realized_week + open_pnl),
        weekly_cap=cfg.max_weekly_loss_pct * led.week_start_balance,
        drawdown=max(dd, Decimal(0)),
        opened_today=led.trades_today + sum(1 for x in s.positions if x.opened_at >= led.day_start),
    )


def _pct(x: Decimal) -> str:
    return f"{x * 100:.2f} %"


def client_order_id(proposal_id: str) -> str:
    """Deterministic order id: the same idea can never produce two orders."""
    return "apx-" + hashlib.sha256(proposal_id.encode()).hexdigest()[:24]


def evaluate(p: Proposal, s: Snapshot, cfg: RiskConfig) -> RiskDecision:
    try:
        return _evaluate(p, s, cfg)
    except Exception as e:  # fail closed
        return RiskDecision(
            p.proposal_id,
            False,
            s.now,
            cfg.config_hash(),
            [Check("internal_error", "Erreur interne du moteur de risque", False, type(e).__name__)],
            ["internal_error"],
            True,
            cfg.risk_per_trade_pct,
            Decimal(0),
        )


def _evaluate(p: Proposal, s: Snapshot, cfg: RiskConfig) -> RiskDecision:
    checks: list[Check] = []

    def check(
        code: str,
        label: str,
        ok: bool,
        value: str = "",
        limit: str = "",
        severity: Literal["block", "critical"] = "block",
    ) -> bool:
        checks.append(Check(code, label, ok, value, limit, severity))
        return ok

    acc = s.account
    eq, bal = acc.equity.amount, acc.balance.amount
    led = s.ledger

    # ------------------------------------------------------------------ system state
    check(
        "mode",
        "Mode de trading autorisé",
        s.mode in (TradingMode.PAPER, TradingMode.SIMULATION),
        s.mode.value,
        "paper / simulation",
    )
    check(
        "kill_switch", "Arrêt d'urgence non engagé", not s.kill_switch, "engagé" if s.kill_switch else "non"
    )
    check("trading_enabled", "Trading activé", s.trading_enabled, "oui" if s.trading_enabled else "non")
    check("duplicate", "Idée pas déjà exécutée", not s.already_submitted)

    # ------------------------------------------------------------------ market & data
    check("market_open", "Marché ouvert", is_market_open(s.now))
    ny = s.now.astimezone(NY)
    roll = datetime.combine(ny.date(), ROLLOVER, tzinfo=NY)
    mins_to_roll = (roll - ny).total_seconds() / 60
    near_roll = 0 <= mins_to_roll <= cfg.rollover_buffer_minutes
    check(
        "rollover",
        "Pas juste avant le rollover quotidien",
        not near_roll,
        f"{mins_to_roll:.0f} min" if near_roll else "",
        f"{cfg.rollover_buffer_minutes} min",
    )
    near_weekly = ny.weekday() == 4 and 0 <= mins_to_roll <= cfg.weekly_close_buffer_minutes
    check(
        "weekly_close",
        "Pas juste avant la fermeture du vendredi",
        not near_weekly,
        f"{mins_to_roll:.0f} min" if near_weekly else "",
        f"{cfg.weekly_close_buffer_minutes} min",
    )
    check(
        "data_quality", "Qualité des données suffisante", s.data_quality_ok, ", ".join(s.data_quality_issues)
    )
    q = s.quote
    age = (s.now - q.ts).total_seconds() if q else None
    check(
        "quote_fresh",
        "Prix récent",
        q is not None and age is not None and age <= cfg.max_quote_age_s,
        "aucun" if age is None else f"{age:.1f} s",
        f"{cfg.max_quote_age_s} s",
    )
    check(
        "spread",
        "Spread acceptable",
        q is not None and q.spread <= cfg.max_spread,
        f"{q.spread}" if q else "—",
        f"{cfg.max_spread}",
    )
    check("news", "Pas d'annonce majeure imminente", s.news_lock_reason is None, s.news_lock_reason or "")

    # ------------------------------------------------------------------ account-level limits
    m = account_metrics(s, cfg)
    daily_loss, daily_cap, dd = m.daily_loss, m.daily_cap, m.drawdown
    check(
        "daily_loss",
        "Perte du jour sous la limite",
        daily_loss < daily_cap,
        f"{daily_loss:.2f} {acc.currency}",
        f"{daily_cap:.2f} {acc.currency} ({_pct(cfg.max_daily_loss_pct)})",
    )
    check(
        "weekly_loss",
        "Perte de la semaine sous la limite",
        m.weekly_loss < m.weekly_cap,
        f"{m.weekly_loss:.2f} {acc.currency}",
        f"{m.weekly_cap:.2f} {acc.currency} ({_pct(cfg.max_weekly_loss_pct)})",
    )
    check(
        "drawdown",
        "Drawdown total sous la limite",
        dd < cfg.max_total_drawdown_pct,
        _pct(dd),
        _pct(cfg.max_total_drawdown_pct),
        severity="critical",
    )
    opened_today = m.opened_today
    check(
        "trades_per_day",
        "Nombre de trades du jour",
        opened_today < cfg.max_trades_per_day,
        str(opened_today),
        str(cfg.max_trades_per_day),
    )
    check(
        "losing_streak",
        "Série de pertes",
        led.consecutive_losses < cfg.max_consecutive_losses,
        str(led.consecutive_losses),
        str(cfg.max_consecutive_losses),
    )
    check(
        "open_positions",
        "Positions ouvertes",
        len(s.positions) < cfg.max_open_positions,
        str(len(s.positions)),
        str(cfg.max_open_positions),
    )
    opposite = any(x.symbol == p.symbol and x.side != p.side for x in s.positions)
    check("no_hedge", "Pas de position opposée ouverte", not opposite)

    # ------------------------------------------------------------------ trade geometry
    sgn = p.side.sign
    stop_dist = (p.entry - p.stop_loss) * sgn
    tp_dist = (p.take_profit - p.entry) * sgn
    geometry_ok = check("stop_side", "Stop du bon côté de l'entrée", stop_dist > 0, f"{stop_dist:.2f}")
    check("target_side", "Objectif du bon côté de l'entrée", tp_dist > 0, f"{tp_dist:.2f}")
    if s.atr is not None and s.atr > 0 and stop_dist > 0:
        in_atr = stop_dist / s.atr
        check(
            "stop_distance",
            "Distance du stop raisonnable (en ATR)",
            cfg.min_stop_atr <= in_atr <= cfg.max_stop_atr,
            f"{in_atr:.2f} ATR",
            f"{cfg.min_stop_atr}–{cfg.max_stop_atr} ATR",
        )
    else:
        check("stop_distance", "Distance du stop raisonnable (en ATR)", False, "ATR indisponible")
    # Costs: spread is paid on the round trip; commission both sides (estimated at entry price per lot)
    net_rr: Decimal | None = None
    if q is not None and geometry_ok and tp_dist > 0:
        spread = q.spread
        per_unit_comm = (s.spec.commission_for(Decimal(1), p.entry) * 2) / s.spec.contract_size
        reward = tp_dist - spread - per_unit_comm
        risk = stop_dist + spread + per_unit_comm
        net_rr = (reward / risk).quantize(Decimal("0.01")) if risk > 0 else Decimal(0)
        check(
            "risk_reward",
            "Rendement/risque après coûts",
            net_rr >= cfg.min_risk_reward,
            f"{net_rr}",
            f"≥ {cfg.min_risk_reward}",
        )
    else:
        check("risk_reward", "Rendement/risque après coûts", False, "incalculable")

    # ------------------------------------------------------------------ risk budget (reduction only)
    base = cfg.risk_per_trade_pct
    eff = base
    reductions: list[str] = []
    mult = min(Decimal(1), max(Decimal(0), s.regime_multiplier))
    if mult < 1:
        eff *= mult
        reductions.append(f"régime de marché × {mult}")
    if dd >= cfg.drawdown_reduce_at * cfg.max_total_drawdown_pct:
        eff /= 2
        reductions.append(f"drawdown {_pct(dd)} : risque divisé par 2")
    capital = min(eq, bal)
    remaining_day = daily_cap - daily_loss
    if capital > 0 and remaining_day > 0 and eff * capital > remaining_day:
        eff = remaining_day / capital
        reductions.append("plafonné au budget de perte restant du jour")

    sizing = None
    if geometry_ok and eff > 0:
        sizing = compute_position_size(
            SizingInput(
                side=p.side,
                entry=p.entry,
                stop_loss=p.stop_loss,
                risk_pct=eff,
                equity=acc.equity,
                balance=acc.balance,
                free_margin=acc.free_margin,
                leverage=acc.leverage,
                slippage_buffer=cfg.max_slippage,
                max_volume=cfg.max_volume_lots,
                min_free_margin_ratio=cfg.min_free_margin_ratio,
            ),
            s.spec,
            s.fx,
            s.now,
        )
        check(
            "sizing",
            "Taille de position calculable",
            sizing.approved,
            f"{sizing.volume} lot" if sizing.approved else ", ".join(sizing.rejections),
        )
    else:
        check("sizing", "Taille de position calculable", False, "géométrie invalide ou budget épuisé")

    failed = [c for c in checks if not c.passed]
    approved = not failed and sizing is not None and sizing.approved
    order = None
    if approved and sizing is not None:
        order = OrderRequest(
            client_order_id=client_order_id(p.proposal_id),
            symbol=p.symbol,
            side=p.side,
            volume=sizing.volume,
            stop_loss=p.stop_loss,
            take_profit=p.take_profit,
            comment=f"{p.source}:{p.proposal_id}",
        )
    return RiskDecision(
        proposal_id=p.proposal_id,
        approved=approved,
        decided_at=s.now,
        config_hash=cfg.config_hash(),
        checks=checks,
        rejections=[c.code for c in failed],
        critical=any(c.severity == "critical" for c in failed),
        risk_pct_base=base,
        risk_pct_effective=eff if approved else Decimal(0),
        reductions=reductions,
        sizing=sizing,
        order=order,
        net_risk_reward=net_rr,
    )
