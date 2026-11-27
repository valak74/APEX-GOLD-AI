"""Operator CLI: key generation, user bootstrap, API server."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys

from sqlalchemy import select

from apex.core.config import get_settings
from apex.db.audit import append_audit
from apex.db.models import User
from apex.db.session import make_engine, make_sessionmaker
from apex.domain.enums import Role
from apex.security.crypto import generate_master_key
from apex.security.passwords import WeakPasswordError, hash_password


def gen_keys() -> None:
    print(f"APEX_JWT_SECRET={secrets.token_urlsafe(48)}")
    print(f"APEX_MASTER_KEY={generate_master_key()}")


async def create_user(email: str, role: Role, password: str) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            if (await session.execute(select(User).where(User.email == email))).scalar_one_or_none():
                sys.exit(f"user {email} already exists")
            session.add(User(email=email, password_hash=hash_password(password), role=role))
            await append_audit(session, "cli", "user_created", target=email, details={"role": role.value})
            await session.commit()
    finally:
        await engine.dispose()
    print(f"created {role.value} {email}")


async def import_dukascopy(symbol: str, start_s: str, end_s: str, force: bool) -> int:
    from datetime import UTC, datetime

    from apex.market.dukascopy import import_ticks, rebuild_bars

    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)
    engine = make_engine(get_settings().database_url)
    maker = make_sessionmaker(engine)
    try:
        rep = await import_ticks(maker, symbol, start, end, force=force)
        print(
            f"heures: {rep.hours_total} · importées {rep.hours_imported} · vides {rep.hours_empty} · "
            f"déjà présentes {rep.hours_skipped_existing} · ticks {rep.ticks}"
        )
        for r in rep.rejected[:20]:
            print(f"  REJETÉ {r}")
        for f in rep.failed[:20]:
            print(f"  ÉCHEC téléchargement {f}")
        bars = await rebuild_bars(maker, "dukascopy", symbol, start, end)
        print(f"bougies calculées : {bars}")
        return 1 if rep.rejected or rep.failed else 0
    finally:
        await engine.dispose()


async def calendar_import(path: str, source: str) -> int:
    import json

    from pydantic import TypeAdapter, ValidationError

    from apex.intel.calendar import EventIn, import_events

    try:
        from pathlib import Path

        raw = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
        events = TypeAdapter(list[EventIn]).validate_python(json.loads(raw))
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        print(f"fichier invalide : {e}")
        return 1
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            n = await import_events(session, events, source)
            await append_audit(session, "cli", "calendar_imported", details={"source": source, "events": n})
            await session.commit()
    finally:
        await engine.dispose()
    print(f"{n} événements importés")
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="apex")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-keys", help="print fresh JWT secret and master key")
    cu = sub.add_parser("create-user", help="create a user (password prompted)")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=[r.value for r in Role], default="viewer")
    cs = sub.add_parser("ctrader-setup", help="link a cTrader DEMO account (OAuth, interactive)")
    cs.add_argument(
        "--redirect-uri",
        default="http://localhost:8080/callback",
        help="must match the Redirect URI registered on openapi.ctrader.com",
    )
    cc = sub.add_parser(
        "ctrader-check", help="connect to the linked cTrader account and print spec + live quotes"
    )
    cc.add_argument(
        "--account", type=int, required=True, help="internal broker account id (from ctrader-setup)"
    )
    cc.add_argument("--seconds", type=int, default=20)
    imp = sub.add_parser("import-dukascopy", help="import Dukascopy historical ticks and build bars")
    imp.add_argument("--symbol", default="XAUUSD")
    imp.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD (UTC)")
    imp.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    imp.add_argument("--force", action="store_true", help="re-download hours already present")
    cal = sub.add_parser("calendar-import", help="import economic events from a JSON file")
    cal.add_argument("file")
    cal.add_argument("--source", default="manual")
    srv = sub.add_parser("serve", help="run the API")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)

    if a.cmd == "gen-keys":
        gen_keys()
    elif a.cmd == "create-user":
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat: "):
            sys.exit("passwords differ")
        try:
            asyncio.run(create_user(a.email.lower(), Role(a.role), pw))
        except WeakPasswordError as e:
            sys.exit(str(e))
    elif a.cmd == "ctrader-setup":
        from apex.broker.ctrader.setup_cli import setup

        asyncio.run(setup(a.redirect_uri))
    elif a.cmd == "ctrader-check":
        from apex.broker.ctrader.setup_cli import check

        sys.exit(asyncio.run(check(a.account, a.seconds)))
    elif a.cmd == "import-dukascopy":
        sys.exit(asyncio.run(import_dukascopy(a.symbol, a.start, a.end, a.force)))
    elif a.cmd == "calendar-import":
        sys.exit(asyncio.run(calendar_import(a.file, a.source)))
    elif a.cmd == "serve":
        import uvicorn

        uvicorn.run("apex.api.main:create_app", factory=True, host=a.host, port=a.port, proxy_headers=True)


if __name__ == "__main__":
    main()
