"""Simulated execution engine shared by the PAPER and SIMULATION adapters.

* PAPER: fed with real-time quotes (``on_quote``) from a market-data source, orders are simulated.
* SIMULATION: fed with historical quotes by a replay loop; the clock follows quote timestamps.

Execution model (deliberately conservative):
* market buy fills at ask + slippage, market sell at bid - slippage; closes at the opposite side;
* stop-loss triggers on the closing side of the quote and fills at that quote (gaps fill worse than SL);
* take-profit fills at the TP price (no positive slippage assumed);
* commission is charged per side at open and close; hedging account (one position per fill);
* stop-out closes the worst position while margin level <= ``stop_out_level_pct``.
Pending limit/stop orders are not implemented yet (capabilities.pending_orders = False).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from apex.broker.base import (
    BrokerAdapter,
    BrokerNotConnectedError,
    Capabilities,
    InvalidModificationError,
    PositionNotFoundError,
    UnknownSymbolError,
)
from apex.core.fx import FxConverter
from apex.core.logging import get_logger
from apex.core.money import Money
from apex.domain.enums import CloseReason, OrderStatus, OrderType, PositionStatus, Side, TradingMode
from apex.domain.models import (
    AccountInfo,
    ClosedTrade,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
    SymbolSpec,
    utcnow,
)

log = get_logger(__name__)


class _Pos:
    __slots__ = ("id", "open_commission", "open_price", "opened_at", "side", "sl", "spec", "tp", "volume")

    def __init__(
        self,
        spec: SymbolSpec,
        side: Side,
        volume: Decimal,
        price: Decimal,
        sl: Decimal | None,
        tp: Decimal | None,
        at: datetime,
    ) -> None:
        self.id = uuid4()
        self.spec, self.side, self.volume, self.open_price = spec, side, volume, price
        self.sl, self.tp, self.opened_at = sl, tp, at
        self.open_commission = Decimal(0)  # in spec.commission_ccy, for the remaining volume


class SimBroker(BrokerAdapter):
    def __init__(
        self,
        *,
        mode: TradingMode,
        account_id: str,
        currency: str,
        initial_balance: Decimal,
        leverage: int,
        specs: list[SymbolSpec],
        slippage: Decimal = Decimal(0),
        max_quote_age: timedelta = timedelta(seconds=10),
        stop_out_level_pct: Decimal = Decimal(50),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if mode is TradingMode.LIVE:
            raise ValueError("SimBroker can never run in LIVE mode")
        self.capabilities = Capabilities(
            name=f"sim-{mode.value}",
            mode=mode,
            push_quotes=True,
            sltp_on_entry=True,
            modify_sltp=True,
            partial_close=True,
            pending_orders=False,
            market_depth=False,
            real_volume=False,
        )
        self.account_id = account_id
        self.currency = currency.upper()
        self.leverage = leverage
        self.slippage = slippage
        self.max_quote_age = max_quote_age
        self.stop_out_level_pct = stop_out_level_pct
        self._balance = Money(amount=initial_balance, ccy=self.currency)
        self._specs = {s.symbol: s for s in specs}
        self._quotes: dict[str, Quote] = {}
        self._positions: dict[UUID, _Pos] = {}
        self._orders: dict[str, OrderResult] = {}
        self.closed_trades: list[ClosedTrade] = []
        self.fx = FxConverter(
            max_age=timedelta(days=3650) if mode is TradingMode.SIMULATION else timedelta(minutes=2)
        )
        self._clock = clock
        self._connected = False
        self._subscribers: list[tuple[set[str], asyncio.Queue[Quote]]] = []

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _require(self) -> None:
        if not self._connected:
            raise BrokerNotConnectedError(self.capabilities.name)

    def now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        if self.mode is TradingMode.SIMULATION and self._quotes:
            return max(q.ts for q in self._quotes.values())
        return utcnow()

    # ------------------------------------------------------------------ market data
    async def on_quote(self, quote: Quote) -> list[ClosedTrade]:
        """Feed a new quote. Returns trades closed by SL/TP/stop-out as a result."""
        self._quotes[quote.symbol] = quote
        self.fx.update(quote)
        for symbols, q in self._subscribers:
            if quote.symbol in symbols:
                q.put_nowait(quote)
        closed = self._check_sl_tp(quote)
        closed += self._check_stop_out()
        return closed

    async def get_market_data(self, symbol: str) -> Quote:
        self._require()
        q = self._quotes.get(symbol)
        if q is None:
            raise UnknownSymbolError(f"no quote for {symbol}")
        return q

    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=10_000)
        entry = (set(symbols), queue)
        self._subscribers.append(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(entry)

    def set_symbol_spec(self, spec: SymbolSpec) -> None:
        """Replace a specification (e.g. placeholder -> broker's real one). Open positions keep theirs."""
        self._specs[spec.symbol] = spec

    async def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        try:
            return self._specs[symbol]
        except KeyError:
            raise UnknownSymbolError(symbol) from None

    # ------------------------------------------------------------------ account
    def _to_acc(self, amount: Decimal, ccy: str, conservative: str = "mid") -> Money:
        return self.fx.convert(Money(amount=amount, ccy=ccy), self.currency, self.now(), conservative)  # type: ignore[arg-type]

    def _close_price(self, p: _Pos) -> Decimal:
        q = self._quotes[p.spec.symbol]
        return q.bid if p.side is Side.BUY else q.ask

    def _unrealized(self, p: _Pos) -> Money:
        diff = (self._close_price(p) - p.open_price) * p.side.sign
        return self._to_acc(diff * p.volume * p.spec.contract_size, p.spec.quote_ccy)

    def _margin(self, spec: SymbolSpec, volume: Decimal, price: Decimal) -> Money:
        rate = spec.margin_rate if spec.margin_rate is not None else Decimal(1) / Decimal(self.leverage)
        return self._to_acc(volume * spec.contract_size * price * rate, spec.quote_ccy, "high")

    def _totals(self) -> tuple[Money, Money]:
        zero = Money(amount=Decimal(0), ccy=self.currency)
        upnl, margin = zero, zero
        for p in self._positions.values():
            upnl = upnl + self._unrealized(p)
            margin = margin + self._margin(p.spec, p.volume, p.open_price)
        return upnl, margin

    async def get_account_info(self) -> AccountInfo:
        self._require()
        upnl, margin = self._totals()
        equity = self._balance + upnl
        return AccountInfo(
            mode=self.mode,
            account_id=self.account_id,
            currency=self.currency,
            balance=self._balance,
            equity=equity,
            margin_used=margin,
            free_margin=equity - margin,
            leverage=self.leverage,
            margin_level_pct=(equity.amount / margin.amount * 100) if margin.amount > 0 else None,
        )

    async def get_positions(self) -> list[Position]:
        self._require()
        return [self._view(p) for p in self._positions.values()]

    def _view(self, p: _Pos) -> Position:
        return Position(
            position_id=p.id,
            symbol=p.spec.symbol,
            side=p.side,
            volume=p.volume,
            open_price=p.open_price,
            stop_loss=p.sl,
            take_profit=p.tp,
            opened_at=p.opened_at,
            status=PositionStatus.OPEN,
            unrealized_pnl=self._unrealized(p),
        )

    # ------------------------------------------------------------------ orders
    def _result(
        self, req: OrderRequest, status: OrderStatus, reason: str | None = None, **kw: object
    ) -> OrderResult:
        res = OrderResult(
            order_id=uuid4(),
            client_order_id=req.client_order_id,
            status=status,
            symbol=req.symbol,
            side=req.side,
            requested_volume=req.volume,
            reject_reason=reason,
            **kw,
        )
        self._orders[req.client_order_id] = res
        if status is OrderStatus.REJECTED:
            log.info(
                "order_rejected", client_order_id=req.client_order_id, reason=reason, mode=self.mode.value
            )
        return res

    @staticmethod
    def _check_levels(
        side: Side, ref: Decimal, sl: Decimal | None, tp: Decimal | None, min_dist: Decimal
    ) -> str | None:
        """SL must be on the losing side and TP on the winning side of ``ref``, both >= ``min_dist`` away."""
        s = side.sign
        if sl is not None:
            dist = (ref - sl) * s
            if dist <= 0 or dist < min_dist:
                return "invalid_stop_loss"
        if tp is not None:
            dist = (tp - ref) * s
            if dist <= 0 or dist < min_dist:
                return "invalid_take_profit"
        return None

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self._require()
        if request.client_order_id in self._orders:  # idempotency: never fill the same order twice
            return self._orders[request.client_order_id]
        spec = self._specs.get(request.symbol)
        if spec is None:
            return self._result(request, OrderStatus.REJECTED, "unknown_symbol")
        if request.order_type is not OrderType.MARKET:
            return self._result(request, OrderStatus.REJECTED, "order_type_not_supported")
        q = self._quotes.get(request.symbol)
        if q is None:
            return self._result(request, OrderStatus.REJECTED, "no_market_data")
        if self.now() - q.ts > self.max_quote_age:
            return self._result(request, OrderStatus.REJECTED, "stale_market_data")
        v = request.volume
        if v < spec.volume_min or v > spec.volume_max or (v / spec.volume_step) % 1 != 0:
            return self._result(request, OrderStatus.REJECTED, "invalid_volume")

        price = q.ask + self.slippage if request.side is Side.BUY else q.bid - self.slippage
        reason = self._check_levels(
            request.side, price, request.stop_loss, request.take_profit, spec.stops_level
        )
        if reason:
            return self._result(request, OrderStatus.REJECTED, reason)

        try:
            margin = self._margin(spec, v, price)
            open_comm = spec.commission_for(v, price)
            commission = self._to_acc(open_comm, spec.commission_ccy, "high")
            info = await self.get_account_info()
        except (LookupError, RuntimeError) as e:  # FX unavailable/stale
            return self._result(request, OrderStatus.REJECTED, f"fx_error: {e}")
        if margin.amount + commission.amount > info.free_margin.amount:
            return self._result(request, OrderStatus.REJECTED, "insufficient_margin")

        pos = _Pos(spec, request.side, v, price, request.stop_loss, request.take_profit, self.now())
        pos.open_commission = open_comm
        self._positions[pos.id] = pos
        self._balance = self._balance - commission
        log.info(
            "order_filled", mode=self.mode.value, side=request.side.value, volume=str(v), price=str(price)
        )
        return self._result(
            request,
            OrderStatus.FILLED,
            filled_volume=v,
            fill_price=price,
            position_id=pos.id,
            commission=commission,
        )

    async def get_order_status(self, client_order_id: str) -> OrderResult | None:
        return self._orders.get(client_order_id)

    async def modify_order(
        self, position_id: UUID, stop_loss: Decimal | None = None, take_profit: Decimal | None = None
    ) -> Position:
        self._require()
        p = self._positions.get(position_id)
        if p is None:
            raise PositionNotFoundError(str(position_id))
        ref = self._close_price(p)
        new_sl = stop_loss if stop_loss is not None else p.sl
        new_tp = take_profit if take_profit is not None else p.tp
        reason = self._check_levels(p.side, ref, new_sl, new_tp, p.spec.stops_level)
        if reason:
            raise InvalidModificationError(reason)
        p.sl, p.tp = new_sl, new_tp
        return self._view(p)

    def _close(
        self, p: _Pos, price: Decimal, reason: CloseReason, volume: Decimal | None = None
    ) -> ClosedTrade:
        vol = p.volume if volume is None else volume
        gross = self._to_acc(
            (price - p.open_price) * p.side.sign * vol * p.spec.contract_size, p.spec.quote_ccy
        )
        comm_close = self._to_acc(p.spec.commission_for(vol, price), p.spec.commission_ccy, "high")
        open_part = p.open_commission * vol / p.volume  # share of the commission already paid at open
        comm_open = self._to_acc(open_part, p.spec.commission_ccy, "high")
        p.open_commission -= open_part
        self._balance = self._balance + gross - comm_close
        if vol >= p.volume:
            del self._positions[p.id]
        else:
            p.volume -= vol
        trade = ClosedTrade(
            position_id=p.id,
            symbol=p.spec.symbol,
            side=p.side,
            volume=vol,
            open_price=p.open_price,
            close_price=price,
            opened_at=p.opened_at,
            closed_at=self.now(),
            reason=reason,
            gross_pnl=gross,
            commission=comm_open + comm_close,
            net_pnl=gross - comm_open - comm_close,
        )
        self.closed_trades.append(trade)
        log.info("position_closed", mode=self.mode.value, reason=reason.value, net_pnl=str(trade.net_pnl))
        return trade

    def _market_close_price(self, p: _Pos) -> Decimal:
        q = self._quotes[p.spec.symbol]
        return q.bid - self.slippage if p.side is Side.BUY else q.ask + self.slippage

    async def close_position(
        self, position_id: UUID, volume: Decimal | None = None, reason: CloseReason = CloseReason.MANUAL
    ) -> ClosedTrade:
        self._require()
        p = self._positions.get(position_id)
        if p is None:
            raise PositionNotFoundError(str(position_id))
        if volume is not None:
            if volume <= 0 or volume > p.volume or (volume / p.spec.volume_step) % 1 != 0:
                raise InvalidModificationError("invalid_close_volume")
            remaining = p.volume - volume
            if 0 < remaining < p.spec.volume_min:
                raise InvalidModificationError("remaining_volume_below_min")
        return self._close(p, self._market_close_price(p), reason, volume)

    async def close_all_positions(self, reason: CloseReason = CloseReason.MANUAL) -> list[ClosedTrade]:
        self._require()
        return [self._close(p, self._market_close_price(p), reason) for p in list(self._positions.values())]

    # ------------------------------------------------------------------ triggers
    def _check_sl_tp(self, q: Quote) -> list[ClosedTrade]:
        out: list[ClosedTrade] = []
        for p in list(self._positions.values()):
            if p.spec.symbol != q.symbol:
                continue
            px = q.bid if p.side is Side.BUY else q.ask
            s = p.side.sign
            if p.sl is not None and (px - p.sl) * s <= 0:
                out.append(self._close(p, px, CloseReason.STOP_LOSS))
            elif p.tp is not None and (px - p.tp) * s >= 0:
                out.append(self._close(p, p.tp, CloseReason.TAKE_PROFIT))
        return out

    def _check_stop_out(self) -> list[ClosedTrade]:
        out: list[ClosedTrade] = []
        while self._positions:
            upnl, margin = self._totals()
            equity = self._balance + upnl
            if margin.amount <= 0 or equity.amount / margin.amount * 100 > self.stop_out_level_pct:
                break
            worst = min(self._positions.values(), key=lambda p: self._unrealized(p).amount)
            out.append(self._close(worst, self._market_close_price(worst), CloseReason.STOP_OUT))
        return out
