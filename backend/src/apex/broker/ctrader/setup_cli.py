"""Interactive cTrader linking and connectivity check (``apex ctrader-setup`` / ``apex ctrader-check``).

Secrets are typed into the operator's own terminal (hidden input) and stored encrypted in the database.
They are never printed and must never be pasted into chats or tickets.
"""

from __future__ import annotations

import asyncio
import getpass
import statistics
import sys
import time
from decimal import Decimal
from typing import Any

from apex.broker.ctrader import proto
from apex.broker.ctrader.adapter import CTraderAdapter, CTraderCredentials
from apex.broker.ctrader.client import CTraderClient
from apex.broker.ctrader.oauth import build_auth_url, exchange_code, extract_code
from apex.broker.ctrader.store import load_ctrader_credentials, save_ctrader_account, token_saver
from apex.core.config import get_settings
from apex.db.session import make_engine, make_sessionmaker
from apex.domain.models import Quote


def _master_key() -> str:
    mk = get_settings().master_key.get_secret_value()
    if not mk:
        sys.exit("APEX_MASTER_KEY is not set — cannot store credentials safely.")
    return mk


async def _list_accounts(client_id: str, client_secret: str, access_token: str) -> list[Any]:
    c = CTraderClient(proto.DEMO_HOST)
    await c.connect()
    try:
        await c.request(
            proto.new("ProtoOAApplicationAuthReq", clientId=client_id, clientSecret=client_secret)
        )
        res: Any = await c.request(
            proto.new("ProtoOAGetAccountListByAccessTokenReq", accessToken=access_token)
        )
        return list(res.ctidTraderAccount)
    finally:
        await c.close()


async def setup(redirect_uri: str) -> None:
    mk = _master_key()
    print("Liaison d'un compte DEMO cTrader. Les secrets saisis ne s'affichent pas.\n")
    client_id = input("Client ID de l'application Open API : ").strip()
    client_secret = getpass.getpass("Client Secret (masqué) : ").strip()
    print("\n1) Ouvrez cette adresse dans votre navigateur et autorisez l'accès :\n")
    print("   " + build_auth_url(client_id, redirect_uri))
    print("\n2) Vous serez redirigé vers une page qui ne se charge pas (c'est normal).")
    print(
        "   Copiez l'adresse COMPLÈTE de la barre du navigateur et collez-la ici"
        " (dans ce terminal uniquement).\n"
    )
    code = extract_code(
        input("Adresse de redirection : ")
    )  # single-use, short-lived, useless without the secret
    tokens = await exchange_code(client_id, client_secret, code, redirect_uri)
    print("✓ Jetons obtenus.")

    accounts = [
        a
        for a in await _list_accounts(client_id, client_secret, tokens.access_token)
        if not (a.HasField("isLive") and a.isLive)
    ]
    if not accounts:
        sys.exit(
            "Aucun compte DEMO trouvé pour ce cTrader ID (les comptes réels sont refusés dans cette version)."
        )
    print("\nComptes DEMO disponibles :")
    for i, a in enumerate(accounts, 1):
        print(
            f"  {i}. compte n°{a.traderLogin if a.HasField('traderLogin') else '?'}"
            f" (ctid {a.ctidTraderAccountId})"
        )
    choice = (
        accounts[int(input("Numéro du compte à lier : ").strip()) - 1] if len(accounts) > 1 else accounts[0]
    )

    creds = CTraderCredentials(
        client_id=client_id,
        client_secret=client_secret,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        ctid_trader_account_id=int(choice.ctidTraderAccountId),
    )
    adapter = CTraderAdapter(creds, reconnect=False)
    await adapter.connect()
    currency, broker = adapter.currency, adapter.broker_name or "ctrader"
    await adapter.disconnect()

    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            acc = await save_ctrader_account(
                session,
                mk,
                actor="cli",
                broker=f"{broker.lower().replace(' ', '-')}-ctrader"[:50],
                ctid_account_id=creds.ctid_trader_account_id,
                currency=currency,
                creds=creds,
            )
    finally:
        await engine.dispose()
    print(f"\n✓ Compte lié : {broker}, devise {currency}. Identifiant interne : {acc.id}")
    print(f"  Activez le flux : APEX_MARKET_DATA_SOURCE=ctrader  APEX_CTRADER_BROKER_ACCOUNT_ID={acc.id}")
    print("  Puis testez : apex ctrader-check --account", acc.id)


async def check(account_id: int, seconds: int) -> int:
    s = get_settings()
    engine = make_engine(s.database_url)
    maker = make_sessionmaker(engine)
    try:
        async with maker() as session:
            creds = await load_ctrader_credentials(session, _master_key(), account_id)
        adapter = CTraderAdapter(
            creds,
            reconnect=False,
            max_gold_leverage=s.ctrader_max_gold_leverage,
            extra_currencies=(s.paper_account_currency,),
            on_tokens_refreshed=token_saver(maker, _master_key(), account_id),
        )
        t0 = time.monotonic()
        await adapter.connect()
        print(f"✓ Connecté et authentifié en {time.monotonic() - t0:.2f}s — {adapter.broker_name}")
        info = await adapter.get_account_info()
        print(f"  Compte {info.account_id} · devise {info.currency} · levier compte 1:{info.leverage}")
        print(f"  Solde {info.balance} · équité {info.equity} · marge utilisée {info.margin_used}")
        spec = await adapter.get_symbol_spec("XAUUSD")
        print("\nSpécification XAUUSD lue chez le broker :")
        print(
            f"  taille de contrat {spec.contract_size} oz/lot · lots min/pas/max {spec.volume_min}/"
            f"{spec.volume_step}/{spec.volume_max} · tick {spec.tick_size}"
        )
        print(
            f"  commission {spec.commission_type} {spec.commission_rate} {spec.commission_ccy} par côté"
            f" (min {spec.min_commission}) · soit {spec.commission_for(Decimal(1), Decimal(4000)):.2f}"
            f" {spec.commission_ccy}/lot/côté à 4000 · distance stop min {spec.stops_level}"
        )
        print(f"  marge supposée {spec.margin_rate} (levier prudent 1:{s.ctrader_max_gold_leverage})")

        quotes: list[Quote] = []

        async def on_q(q: Quote) -> None:
            if q.symbol == "XAUUSD":
                quotes.append(q)

        adapter.add_quote_listener(on_q)
        print(f"\nÉcoute des prix pendant {seconds}s…")
        await asyncio.sleep(seconds)
        await adapter.disconnect()
        if not quotes:
            print("✗ Aucun prix reçu (marché fermé ? week-end ?)")
            return 2
        spreads = [float(q.spread) for q in quotes]
        last = quotes[-1]
        print(f"✓ {len(quotes)} prix XAUUSD · dernier bid {last.bid} / ask {last.ask}")
        print(f"  spread médian {statistics.median(spreads):.2f} $ · max {max(spreads):.2f} $")
        return 0
    finally:
        await engine.dispose()
