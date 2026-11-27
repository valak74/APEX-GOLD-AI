"""cTrader Open API broker adapter — READ-ONLY in this build.

What it does: authenticates (app + account, refreshing an expired access token), reads the account
(currency, balance, leverage, positions, used margin, unrealized P&L), reads the real XAUUSD symbol
specification, streams live XAUUSD quotes plus the FX pair needed to convert USD into the account
currency, and reconnects with backoff.

What it does NOT do yet: send, modify or close orders. Those methods raise ``TradingDisabledError``.
Live (real-money) cTrader accounts are refused.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from apex.broker.base import (
    BrokerAdapter,
    BrokerError,
    BrokerNotConnectedError,
    Capabilities,
    UnknownSymbolError,
)
from apex.broker.ctrader import proto
from apex.broker.ctrader.client import CTraderApiError, CTraderClient
from apex.broker.ctrader.mapping import (
    canonical_name,
    find_symbol,
    money_from_int,
    price_from_int,
    side_from_proto,
    symbol_spec_from_proto,
    ts_from_ms,
)
from apex.core.fx import FxConverter
from apex.core.logging import get_logger
from apex.core.money import Money
from apex.domain.enums import CloseReason, TradingMode
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

TOKEN_ERRORS = {"CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED"}
_POS_NS = UUID("6f1d2b1e-6a43-4c55-9a2e-1b9f0d4c7a10")


class TradingDisabledError(BrokerError):
    pass


@dataclass
class CTraderCredentials:
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    ctid_trader_account_id: int
    environment: str = "demo"  # "demo" | "live"

    def __repr__(self) -> str:  # never print secrets
        return f"CTraderCredentials(account={self.ctid_trader_account_id}, env={self.environment})"


TokenSaver = Callable[[str, str], Awaitable[None]]
QuoteListener = Callable[[Quote], Awaitable[Any]]
ClientFactory = Callable[[str, int], CTraderClient]


class CTraderAdapter(BrokerAdapter):
    def __init__(
        self,
        creds: CTraderCredentials,
        *,
        client_factory: ClientFactory | None = None,
        host: str | None = None,
        port: int = proto.PORT,
        max_gold_leverage: int = 20,
        on_tokens_refreshed: TokenSaver | None = None,
        reconnect: bool = True,
        extra_currencies: tuple[str, ...] = (),
    ) -> None:
        if creds.environment != "demo":
            raise ValueError("only cTrader DEMO accounts are allowed in this build")
        self.creds = creds
        self.capabilities = Capabilities(
            name="ctrader-demo-readonly",
            mode=TradingMode.PAPER,
            push_quotes=True,
            sltp_on_entry=True,
            modify_sltp=True,
            partial_close=True,
            pending_orders=True,
            market_depth=False,
            real_volume=False,
        )
        self._factory = client_factory or (lambda h, p: CTraderClient(h, p))
        self._host = host or proto.DEMO_HOST
        self._port = port
        self._max_gold_leverage = max_gold_leverage
        self._on_tokens = on_tokens_refreshed
        self._reconnect = reconnect
        self._extra_ccys = tuple(c.upper() for c in extra_currencies)
        self._client: CTraderClient | None = None
        self._pump: asyncio.Task[None] | None = None
        self._stopping = False
        self.assets: dict[int, str] = {}
        self.symbol_names: dict[int, str] = {}  # symbolId -> canonical name
        self.symbol_ids: dict[str, int] = {}
        self.specs: dict[str, SymbolSpec] = {}
        self.currency: str = ""
        self.money_digits: int | None = None
        self.leverage: int = 0
        self.broker_name: str = ""
        self.fx = FxConverter()
        self._quotes: dict[str, Quote] = {}
        self._partial: dict[int, tuple[int | None, int | None]] = {}
        self._listeners: list[QuoteListener] = []
        self._subscribers: list[tuple[set[str], asyncio.Queue[Quote]]] = []
        self.connected_event = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected and self.connected_event.is_set()

    def _c(self) -> CTraderClient:
        if self._client is None or not self._client.connected:
            raise BrokerNotConnectedError("ctrader not connected")
        return self._client

    def add_quote_listener(self, listener: QuoteListener) -> None:
        self._listeners.append(listener)

    async def connect(self) -> None:
        self._stopping = False
        await self._open_session()
        self._pump = asyncio.create_task(self._pump_loop())

    async def disconnect(self) -> None:
        self._stopping = True
        self.connected_event.clear()
        if self._pump:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
        if self._client:
            await self._client.close()

    async def _open_session(self) -> None:
        self.connected_event.clear()
        client = self._factory(self._host, self._port)
        await client.connect()
        self._client = client
        await client.request(
            proto.new(
                "ProtoOAApplicationAuthReq",
                clientId=self.creds.client_id,
                clientSecret=self.creds.client_secret,
            )
        )
        await self._account_auth()
        await self._load_reference_data()
        await self._subscribe()
        self.connected_event.set()
        log.info(
            "ctrader_session_ready",
            account=self.creds.ctid_trader_account_id,
            currency=self.currency,
            broker=self.broker_name,
        )

    async def _account_auth(self) -> None:
        acc = self.creds.ctid_trader_account_id
        try:
            await self._c().request(
                proto.new(
                    "ProtoOAAccountAuthReq", ctidTraderAccountId=acc, accessToken=self.creds.access_token
                )
            )
        except CTraderApiError as e:
            if e.code not in TOKEN_ERRORS:
                raise
            log.info("ctrader_token_refresh", reason=e.code)
            res: Any = await self._c().request(
                proto.new("ProtoOARefreshTokenReq", refreshToken=self.creds.refresh_token)
            )
            self.creds.access_token, self.creds.refresh_token = res.accessToken, res.refreshToken
            if self._on_tokens:
                await self._on_tokens(res.accessToken, res.refreshToken)
            await self._c().request(
                proto.new(
                    "ProtoOAAccountAuthReq", ctidTraderAccountId=acc, accessToken=self.creds.access_token
                )
            )

    async def _load_reference_data(self) -> None:
        acc = self.creds.ctid_trader_account_id
        c = self._c()
        trader: Any = (await c.request(proto.new("ProtoOATraderReq", ctidTraderAccountId=acc))).trader  # type: ignore[attr-defined]
        assets: Any = await c.request(proto.new("ProtoOAAssetListReq", ctidTraderAccountId=acc))
        self.assets = {int(a.assetId): a.name for a in assets.asset}
        self.currency = self.assets[int(trader.depositAssetId)]
        self.money_digits = int(trader.moneyDigits) if trader.HasField("moneyDigits") else None
        self.leverage = int(trader.leverageInCents) // 100 if trader.HasField("leverageInCents") else 1
        self.broker_name = trader.brokerName if trader.HasField("brokerName") else ""

        lights: Any = await c.request(proto.new("ProtoOASymbolsListReq", ctidTraderAccountId=acc))
        lights_list = list(lights.symbol)
        self.symbol_names = {}
        for s in lights_list:
            name = canonical_name(s, self.assets)
            if name:
                self.symbol_names[int(s.symbolId)] = name
        gold = find_symbol(lights_list, self.assets, "XAU", "USD")
        wanted = [gold]
        for ccy in dict.fromkeys((self.currency, *self._extra_ccys)):  # FX needed to convert USD into ccy
            if ccy == "USD":
                continue
            try:
                wanted.append(find_symbol(lights_list, self.assets, ccy, "USD"))
            except BrokerError:
                wanted.append(find_symbol(lights_list, self.assets, "USD", ccy))
        self.symbol_ids = {self.symbol_names[int(s.symbolId)]: int(s.symbolId) for s in wanted}

        full: Any = await c.request(
            proto.new("ProtoOASymbolByIdReq", ctidTraderAccountId=acc, symbolId=[int(gold.symbolId)])
        )
        cap = min(self.leverage, self._max_gold_leverage) if self.leverage else self._max_gold_leverage
        self.specs = {"XAUUSD": symbol_spec_from_proto(full.symbol[0], gold, self.assets, max_leverage=cap)}

    async def _subscribe(self) -> None:
        await self._c().request(
            proto.new(
                "ProtoOASubscribeSpotsReq",
                ctidTraderAccountId=self.creds.ctid_trader_account_id,
                symbolId=list(self.symbol_ids.values()),
                subscribeToSpotTimestamp=True,
            )
        )

    # ------------------------------------------------------------------ event pump & reconnect
    async def _pump_loop(self) -> None:
        backoff = 1.0
        while not self._stopping:
            client = self._client
            assert client is not None
            env = await client.events.get()
            if env is None or env.name in (
                "ProtoOAClientDisconnectEvent",
                "ProtoOAAccountDisconnectEvent",
                "ProtoOAAccountsTokenInvalidatedEvent",
            ):
                self.connected_event.clear()
                log.warning("ctrader_session_lost", cause=env.name if env else "connection_closed")
                if not self._reconnect or self._stopping:
                    return
                with contextlib.suppress(Exception):
                    await client.close()
                while not self._stopping:
                    await asyncio.sleep(backoff)
                    try:
                        await self._open_session()
                        backoff = 1.0
                        break
                    except Exception as e:
                        log.warning("ctrader_reconnect_failed", error=type(e).__name__, retry_in=backoff)
                        backoff = min(backoff * 2, 60.0)
                continue
            if env.name == "ProtoOASpotEvent":
                try:
                    await self._on_spot(env.message)
                except Exception as e:  # one bad message must never stop the feed
                    log.error("ctrader_spot_handling_failed", error=type(e).__name__)

    async def _on_spot(self, m: Any) -> None:
        sid = int(m.symbolId)
        name = self.symbol_names.get(sid)
        if name is None:
            return
        prev_bid, prev_ask = self._partial.get(sid, (None, None))
        bid = int(m.bid) if m.HasField("bid") else prev_bid  # cTrader may send only the side that changed
        ask = int(m.ask) if m.HasField("ask") else prev_ask
        self._partial[sid] = (bid, ask)
        if bid is None or ask is None:
            return
        ts = ts_from_ms(int(m.timestamp)) if m.HasField("timestamp") else utcnow()
        try:
            q = Quote(symbol=name, bid=price_from_int(bid), ask=price_from_int(ask), ts=ts)
        except ValueError as e:  # crossed/invalid quote: drop it, never propagate bad data
            log.warning("ctrader_bad_quote", symbol=name, error=str(e))
            return
        self._quotes[name] = q
        self.fx.update(q)
        for symbols, queue in self._subscribers:
            if name in symbols:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(q)
        for listener in self._listeners:
            try:
                await listener(q)
            except Exception as e:
                log.error("quote_listener_failed", error=type(e).__name__)

    # ------------------------------------------------------------------ reads
    async def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        try:
            return self.specs[symbol]
        except KeyError:
            raise UnknownSymbolError(symbol) from None

    async def get_market_data(self, symbol: str) -> Quote:
        q = self._quotes.get(symbol)
        if q is None:
            raise UnknownSymbolError(f"no quote yet for {symbol}")
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

    async def _reconcile(self) -> Any:
        return await self._c().request(
            proto.new("ProtoOAReconcileReq", ctidTraderAccountId=self.creds.ctid_trader_account_id)
        )

    async def get_positions(self) -> list[Position]:
        rec = await self._reconcile()
        pnl = await self._unrealized()
        out = []
        for p in rec.position:
            td = p.tradeData
            name = self.symbol_names.get(int(td.symbolId), str(td.symbolId))
            spec = self.specs.get(name)
            lot = spec.contract_size * proto.VOLUME_SCALE if spec else None
            out.append(
                Position(
                    position_id=uuid5(_POS_NS, str(p.positionId)),
                    symbol=name,
                    side=side_from_proto(int(td.tradeSide)),
                    volume=(Decimal(td.volume) / lot) if lot else Decimal(td.volume) / proto.VOLUME_SCALE,
                    open_price=Decimal(str(p.price)),
                    stop_loss=Decimal(str(p.stopLoss)) if p.HasField("stopLoss") else None,
                    take_profit=Decimal(str(p.takeProfit)) if p.HasField("takeProfit") else None,
                    opened_at=ts_from_ms(int(td.openTimestamp)) if td.HasField("openTimestamp") else utcnow(),
                    unrealized_pnl=Money(amount=pnl.get(int(p.positionId), Decimal(0)), ccy=self.currency),
                )
            )
        return out

    async def _unrealized(self) -> dict[int, Decimal]:
        res: Any = await self._c().request(
            proto.new(
                "ProtoOAGetPositionUnrealizedPnLReq", ctidTraderAccountId=self.creds.ctid_trader_account_id
            )
        )
        digits = int(res.moneyDigits)
        return {
            int(x.positionId): money_from_int(int(x.netUnrealizedPnL), digits)
            for x in res.positionUnrealizedPnL
        }

    async def get_account_info(self) -> AccountInfo:
        c = self._c()
        trader: Any = (
            await c.request(
                proto.new(  # type: ignore[attr-defined]
                    "ProtoOATraderReq", ctidTraderAccountId=self.creds.ctid_trader_account_id
                )
            )
        ).trader
        digits = int(trader.moneyDigits) if trader.HasField("moneyDigits") else self.money_digits
        balance = money_from_int(int(trader.balance), digits)
        rec = await self._reconcile()
        # Sum of per-position margins: an upper bound for hedged accounts (exact for one position).
        used = sum(
            (
                money_from_int(int(p.usedMargin), int(p.moneyDigits) if p.HasField("moneyDigits") else digits)
                for p in rec.position
                if p.HasField("usedMargin")
            ),
            Decimal(0),
        )
        upnl = sum((await self._unrealized()).values(), Decimal(0))
        equity = balance + upnl
        ccy = self.currency
        return AccountInfo(
            mode=self.mode,
            account_id=f"ctrader-{self.creds.ctid_trader_account_id}",
            currency=ccy,
            balance=Money(amount=balance, ccy=ccy),
            equity=Money(amount=equity, ccy=ccy),
            margin_used=Money(amount=used, ccy=ccy),
            free_margin=Money(amount=equity - used, ccy=ccy),
            leverage=self.leverage,
            margin_level_pct=(equity / used * 100) if used > 0 else None,
        )

    # ------------------------------------------------------------------ trading: disabled in this build
    def _disabled(self) -> TradingDisabledError:
        return TradingDisabledError(
            "cTrader adapter is read-only in this build (order sending arrives later)"
        )

    async def place_order(self, request: OrderRequest) -> OrderResult:
        raise self._disabled()

    async def modify_order(
        self, position_id: UUID, stop_loss: Decimal | None = None, take_profit: Decimal | None = None
    ) -> Position:
        raise self._disabled()

    async def close_position(
        self, position_id: UUID, volume: Decimal | None = None, reason: CloseReason = CloseReason.MANUAL
    ) -> ClosedTrade:
        raise self._disabled()

    async def close_all_positions(self, reason: CloseReason = CloseReason.MANUAL) -> list[ClosedTrade]:
        raise self._disabled()

    async def get_order_status(self, client_order_id: str) -> OrderResult | None:
        return None
