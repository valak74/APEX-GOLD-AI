"""Asyncio client for the cTrader Open API (Protobuf over TCP+TLS).

* request/response correlation via ``clientMsgId``;
* unsolicited messages (spot, execution, disconnect events) go to ``events``;
* heartbeat every ``heartbeat_s`` seconds (the server drops idle connections);
* client-side rate limit below cTrader's documented per-connection limits;
* any read error closes the client and fails all pending requests (callers reconnect).

Secrets inside messages (client secret, tokens) are never logged: only message names are.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
import time
import uuid
from typing import Any

from google.protobuf.message import Message

from apex.broker.base import BrokerError, BrokerNotConnectedError
from apex.broker.ctrader import proto
from apex.core.logging import get_logger

log = get_logger(__name__)

ERROR_MESSAGES = {"ProtoOAErrorRes", "ProtoErrorRes"}


class CTraderApiError(BrokerError):
    def __init__(self, code: str, description: str = "") -> None:
        super().__init__(f"{code}: {description}" if description else code)
        self.code, self.description = code, description


class _RateLimiter:
    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


class CTraderClient:
    def __init__(
        self,
        host: str,
        port: int = proto.PORT,
        *,
        ssl_context: ssl.SSLContext | None = None,
        use_tls: bool = True,
        heartbeat_s: float = 10.0,
        request_timeout_s: float = 15.0,
        max_requests_per_second: float = 30.0,
    ) -> None:
        self.host, self.port = host, port
        self._ssl: ssl.SSLContext | None = (ssl_context or ssl.create_default_context()) if use_tls else None
        self.heartbeat_s = heartbeat_s
        self.request_timeout_s = request_timeout_s
        self._limiter = _RateLimiter(max_requests_per_second)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future[proto.Envelope]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._write_lock = asyncio.Lock()
        self.events: asyncio.Queue[proto.Envelope | None] = asyncio.Queue(maxsize=100_000)
        self.connected = False
        self.last_message_at: float | None = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.host, self.port, ssl=self._ssl, server_hostname=self.host if self._ssl else None
            ),
            timeout=self.request_timeout_s,
        )
        self.connected = True
        self._tasks = [asyncio.create_task(self._read_loop()), asyncio.create_task(self._heartbeat_loop())]
        log.info("ctrader_connected", host=self.host, port=self.port)

    async def close(self) -> None:
        self._shutdown(BrokerNotConnectedError("client closed"))
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []

    def _shutdown(self, exc: Exception) -> None:
        if not self.connected and self._writer is None:
            return
        self.connected = False
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        with contextlib.suppress(asyncio.QueueFull):
            self.events.put_nowait(None)  # sentinel: stream ended

    async def _send(self, msg: Message, client_msg_id: str | None = None) -> None:
        if not self.connected or self._writer is None:
            raise BrokerNotConnectedError("ctrader client not connected")
        frame = proto.encode_frame(msg, client_msg_id)
        async with self._write_lock:
            self._writer.write(frame)
            await self._writer.drain()

    async def send_event(self, msg: Message) -> None:
        await self._send(msg)

    async def request(self, msg: Message, timeout_s: float | None = None) -> Message:
        """Send a request and wait for its response. Raises ``CTraderApiError`` on an error response."""
        await self._limiter.wait()
        cid = uuid.uuid4().hex
        fut: asyncio.Future[proto.Envelope] = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        try:
            await self._send(msg, cid)
            env = await asyncio.wait_for(fut, timeout_s or self.request_timeout_s)
        except TimeoutError:
            raise CTraderApiError("TIMEOUT", f"no response to {msg.DESCRIPTOR.name}") from None
        finally:
            self._pending.pop(cid, None)
        if env.name in ERROR_MESSAGES:
            m: Any = env.message
            raise CTraderApiError(m.errorCode, m.description if m.HasField("description") else "")
        if env.message is None:
            raise CTraderApiError("UNKNOWN_PAYLOAD", str(env.payload_type))
        return env.message

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                header = await self._reader.readexactly(4)
                body = await self._reader.readexactly(proto.frame_length(header))
                env = proto.decode_body(body)
                self.last_message_at = time.monotonic()
                if env.name == "ProtoHeartbeatEvent":
                    continue
                fut = self._pending.get(env.client_msg_id) if env.client_msg_id else None
                if fut is not None and not fut.done():
                    fut.set_result(env)
                else:
                    try:
                        self.events.put_nowait(env)
                    except asyncio.QueueFull:
                        log.warning("ctrader_event_queue_full", dropped=env.name)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError) as e:
            log.warning("ctrader_disconnected", reason=type(e).__name__)
            self._shutdown(BrokerNotConnectedError(f"connection lost: {type(e).__name__}"))
        except asyncio.CancelledError:
            raise

    async def _heartbeat_loop(self) -> None:
        while self.connected:
            await asyncio.sleep(self.heartbeat_s)
            try:
                await self._send(proto.new("ProtoHeartbeatEvent"))
            except BrokerNotConnectedError:
                return
            except (ConnectionError, OSError):
                self._shutdown(BrokerNotConnectedError("heartbeat write failed"))
                return
