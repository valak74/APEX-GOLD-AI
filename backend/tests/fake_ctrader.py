"""In-process fake cTrader Open API server speaking the real wire format (no TLS) for tests."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from apex.broker.ctrader import proto

ASSETS = {1: "USD", 2: "EUR", 3: "XAU", 4: "GBP"}
# symbolId, name, base, quote
LIGHTS = [(41, "XAUUSD", 3, 1), (1, "EURUSD", 2, 1), (2, "GBPUSD", 4, 1), (99, "XAUUSD.x", 3, 1)]


def gold_symbol(**over: Any) -> Any:
    fields: dict[str, Any] = dict(
        symbolId=41,
        digits=2,
        pipPosition=1,
        lotSize=10_000,
        minVolume=100,
        stepVolume=100,
        maxVolume=5_000_000,
        commissionType=proto.enum_value("ProtoOACommissionType", "USD_PER_MILLION_USD"),
        preciseTradingCommissionRate=3_000_000_000,  # 30 USD per million
        preciseMinCommission=0,
        slDistance=0,
        distanceSetIn=proto.enum_value("ProtoOASymbolDistanceType", "SYMBOL_DISTANCE_IN_POINTS"),
        tradingMode=proto.enum_value("ProtoOATradingMode", "ENABLED"),
    )
    fields.update(over)
    return proto.new("ProtoOASymbol", **fields)


class FakeCTrader:
    def __init__(self) -> None:
        self.client_id, self.client_secret = "cid", "csecret"
        self.access_token, self.refresh_token = "atoken", "rtoken"
        self.account_id = 777
        self.deposit_asset = 2  # EUR
        self.balance_cents = 1_000_000  # 10 000.00
        self.token_expired = False
        self.positions: list[Any] = []
        self.pnl: dict[int, int] = {}
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.Server | None = None
        self.port = 0
        self.gold = gold_symbol()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        await self.drop_all()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def drop_all(self) -> None:
        for w in list(self.writers):
            w.close()
            with contextlib.suppress(Exception):
                await w.wait_closed()
        self.writers.clear()

    async def push_spot(
        self, symbol_id: int, bid: int | None, ask: int | None, ts_ms: int | None = None
    ) -> None:
        kw: dict[str, Any] = {"ctidTraderAccountId": self.account_id, "symbolId": symbol_id}
        if bid is not None:
            kw["bid"] = bid
        if ask is not None:
            kw["ask"] = ask
        if ts_ms is not None:
            kw["timestamp"] = ts_ms
        frame = proto.encode_frame(proto.new("ProtoOASpotEvent", **kw))
        for w in self.writers:
            w.write(frame)
            await w.drain()

    def _err(self, code: str, desc: str = "") -> Any:
        return proto.new("ProtoOAErrorRes", errorCode=code, description=desc)

    def _respond(self, name: str, m: Any) -> Any:
        acc = self.account_id
        if name == "ProtoOAApplicationAuthReq":
            if (m.clientId, m.clientSecret) != (self.client_id, self.client_secret):
                return self._err("CH_CLIENT_AUTH_FAILURE")
            return proto.new("ProtoOAApplicationAuthRes")
        if name == "ProtoOAAccountAuthReq":
            if self.token_expired or m.accessToken != self.access_token:
                return self._err("CH_ACCESS_TOKEN_INVALID", "expired")
            return proto.new("ProtoOAAccountAuthRes", ctidTraderAccountId=acc)
        if name == "ProtoOARefreshTokenReq":
            if m.refreshToken != self.refresh_token:
                return self._err("CH_ACCESS_TOKEN_INVALID")
            self.access_token, self.refresh_token, self.token_expired = "atoken2", "rtoken2", False
            return proto.new(
                "ProtoOARefreshTokenRes",
                accessToken="atoken2",
                tokenType="bearer",
                expiresIn=2_628_000,
                refreshToken="rtoken2",
            )
        if name == "ProtoOATraderReq":
            t = proto.new(
                "ProtoOATrader",
                ctidTraderAccountId=acc,
                balance=self.balance_cents,
                depositAssetId=self.deposit_asset,
                leverageInCents=3000,
                moneyDigits=2,
                brokerName="FakeBroker",
            )
            return proto.new("ProtoOATraderRes", ctidTraderAccountId=acc, trader=t)
        if name == "ProtoOAAssetListReq":
            return proto.new(
                "ProtoOAAssetListRes",
                ctidTraderAccountId=acc,
                asset=[proto.new("ProtoOAAsset", assetId=k, name=v) for k, v in ASSETS.items()],
            )
        if name == "ProtoOASymbolsListReq":
            return proto.new(
                "ProtoOASymbolsListRes",
                ctidTraderAccountId=acc,
                symbol=[
                    proto.new(
                        "ProtoOALightSymbol",
                        symbolId=i,
                        symbolName=n,
                        enabled=True,
                        baseAssetId=b,
                        quoteAssetId=q,
                    )
                    for i, n, b, q in LIGHTS
                ],
            )
        if name == "ProtoOASymbolByIdReq":
            return proto.new("ProtoOASymbolByIdRes", ctidTraderAccountId=acc, symbol=[self.gold])
        if name == "ProtoOASubscribeSpotsReq":
            return proto.new("ProtoOASubscribeSpotsRes", ctidTraderAccountId=acc)
        if name == "ProtoOAReconcileReq":
            return proto.new("ProtoOAReconcileRes", ctidTraderAccountId=acc, position=self.positions)
        if name == "ProtoOAGetPositionUnrealizedPnLReq":
            return proto.new(
                "ProtoOAGetPositionUnrealizedPnLRes",
                ctidTraderAccountId=acc,
                moneyDigits=2,
                positionUnrealizedPnL=[
                    proto.new(
                        "ProtoOAPositionUnrealizedPnL", positionId=k, grossUnrealizedPnL=v, netUnrealizedPnL=v
                    )
                    for k, v in self.pnl.items()
                ],
            )
        return self._err("UNSUPPORTED_IN_FAKE", name)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            while True:
                header = await reader.readexactly(4)
                env = proto.decode_body(await reader.readexactly(proto.frame_length(header)))
                if env.name == "ProtoHeartbeatEvent":
                    continue
                assert env.name is not None
                self.received.append(env.name)
                writer.write(proto.encode_frame(self._respond(env.name, env.message), env.client_msg_id))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
