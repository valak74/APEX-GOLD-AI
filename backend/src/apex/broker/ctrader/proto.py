"""cTrader Open API message definitions and wire framing.

Message classes are built at runtime from ``openapi_descriptors.binpb``, a FileDescriptorSet extracted
from Spotware's official ``ctrader-open-api`` package (MIT, see OPENAPI_PROTO_LICENSE). This keeps the
wire format exactly as Spotware defines it without depending on their Twisted-based client.

Wire format (TCP+TLS, port 5035): 4-byte big-endian length, then a serialized ``ProtoMessage`` whose
``payload`` is the serialized inner message identified by ``payloadType``.
"""

from __future__ import annotations

import struct
from functools import cache
from importlib.resources import files
from typing import Any

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import Message

DEMO_HOST = "demo.ctraderapi.com"
LIVE_HOST = "live.ctraderapi.com"
PORT = 5035
AUTH_URI = "https://openapi.ctrader.com/apps/auth"
TOKEN_URI = "https://openapi.ctrader.com/apps/token"  # noqa: S105 - URL, not a secret

MAX_FRAME = 16 * 1024 * 1024  # defensive cap on a single frame

# cTrader fixed-point conventions
PRICE_SCALE = 100_000  # spot bid/ask and trendbar prices are integers in 1/100000 of a unit
VOLUME_SCALE = 100  # volumes are in "cents" of units: 100 = 1 unit of base asset
PRECISE_RATE_SCALE = 100_000_000  # preciseTradingCommissionRate / preciseMinCommission (non-percentage types)
PRECISE_PCT_SCALE = 100_000  # preciseTradingCommissionRate for PERCENTAGE_OF_VALUE


@cache
def _pool() -> descriptor_pool.DescriptorPool:
    data = files("apex.broker.ctrader").joinpath("openapi_descriptors.binpb").read_bytes()
    fds = descriptor_pb2.FileDescriptorSet.FromString(data)
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)
    return pool


@cache
def message_class(name: str) -> type[Message]:
    return message_factory.GetMessageClass(_pool().FindMessageTypeByName(name))


@cache
def enum_value(enum: str, name: str) -> int:
    return int(_pool().FindEnumTypeByName(enum).values_by_name[name].number)


@cache
def enum_name(enum: str, number: int) -> str:
    v = _pool().FindEnumTypeByName(enum).values_by_number.get(number)
    return v.name if v is not None else str(number)


@cache
def _payload_type_of(name: str) -> int:
    """Payload type of a message, read from its ``payloadType`` field default (as Spotware defines it)."""
    desc = _pool().FindMessageTypeByName(name)
    field = desc.fields_by_name.get("payloadType")
    if field is None or not field.has_default_value:
        raise KeyError(f"{name} has no default payloadType")
    return int(field.default_value)


@cache
def _name_of_payload_type() -> dict[int, str]:
    out: dict[int, str] = {}
    pool = _pool()
    for fname in ("OpenApiMessages.proto", "OpenApiCommonMessages.proto"):
        for desc in pool.FindFileByName(fname).message_types_by_name.values():
            f = desc.fields_by_name.get("payloadType")
            if f is not None and f.has_default_value:
                out[int(f.default_value)] = desc.name
    return out


def payload_type(name: str) -> int:
    return _payload_type_of(name)


def new(message_name: str, /, **fields: Any) -> Message:
    return message_class(message_name)(**fields)


def encode_frame(msg: Message, client_msg_id: str | None = None) -> bytes:
    name = msg.DESCRIPTOR.name
    wrapper: Any = message_class("ProtoMessage")()
    wrapper.payloadType = _payload_type_of(name)
    wrapper.payload = msg.SerializeToString()
    if client_msg_id is not None:
        wrapper.clientMsgId = client_msg_id
    body: bytes = wrapper.SerializeToString()
    return struct.pack(">I", len(body)) + body


class Envelope:
    """A decoded frame: payload type, optional clientMsgId, and the inner message (if known)."""

    __slots__ = ("client_msg_id", "message", "name", "payload_type")

    def __init__(
        self, payload_type: int, client_msg_id: str | None, name: str | None, message: Message | None
    ):
        self.payload_type, self.client_msg_id, self.name, self.message = (
            payload_type,
            client_msg_id,
            name,
            message,
        )

    def __repr__(self) -> str:
        return f"Envelope({self.name or self.payload_type}, id={self.client_msg_id})"


def decode_body(body: bytes) -> Envelope:
    wrapper: Any = message_class("ProtoMessage").FromString(body)
    pt = int(wrapper.payloadType)
    cid = wrapper.clientMsgId if wrapper.HasField("clientMsgId") else None
    name = _name_of_payload_type().get(pt)
    msg = message_class(name).FromString(wrapper.payload) if name else None
    return Envelope(pt, cid, name, msg)


def frame_length(header: bytes) -> int:
    (n,) = struct.unpack(">I", header)
    if n > MAX_FRAME:
        raise ValueError(f"frame too large: {n} bytes")
    return int(n)
