"""cTrader Open API OAuth2 helpers (authorization-code flow, as in Spotware's official client)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from apex.broker.base import BrokerError
from apex.broker.ctrader.proto import AUTH_URI, TOKEN_URI


class OAuthError(BrokerError):
    pass


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int | None

    def __repr__(self) -> str:
        return f"Tokens(expires_in={self.expires_in})"


def build_auth_url(client_id: str, redirect_uri: str, scope: str = "trading") -> str:
    return f"{AUTH_URI}?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'scope': scope})}"


def extract_code(pasted: str) -> str:
    """Accept either the full redirect URL (``...callback?code=XYZ``) or the bare code."""
    pasted = pasted.strip()
    if "://" in pasted or pasted.startswith("/") or "code=" in pasted:
        query = parse_qs(urlparse(pasted).query)
        if "error" in query:
            raise OAuthError(f"authorization refused: {query['error'][0]}")
        codes = query.get("code")
        if not codes:
            raise OAuthError("no 'code' parameter in the pasted URL")
        return codes[0]
    if not pasted:
        raise OAuthError("empty code")
    return pasted


def _parse_tokens(data: dict[str, Any]) -> Tokens:
    if data.get("errorCode"):
        raise OAuthError(f"{data['errorCode']}: {data.get('description') or ''}".strip())
    access = data.get("accessToken") or data.get("access_token")
    refresh = data.get("refreshToken") or data.get("refresh_token")
    if not access or not refresh:
        raise OAuthError(f"unexpected token response keys: {sorted(data)}")
    expires = data.get("expiresIn") or data.get("expires_in")
    return Tokens(access_token=access, refresh_token=refresh, expires_in=int(expires) if expires else None)


async def exchange_code(
    client_id: str, client_secret: str, code: str, redirect_uri: str, http: httpx.AsyncClient | None = None
) -> Tokens:
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    owns = http is None
    http = http or httpx.AsyncClient(timeout=20)
    try:
        r = await http.get(TOKEN_URI, params=params)
    except httpx.HTTPError as e:
        raise OAuthError(f"token endpoint unreachable: {type(e).__name__}") from None
    finally:
        if owns:
            await http.aclose()
    if r.status_code >= 400:
        raise OAuthError(f"token endpoint returned HTTP {r.status_code}")
    return _parse_tokens(r.json())
