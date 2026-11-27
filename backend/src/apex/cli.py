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


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="apex")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-keys", help="print fresh JWT secret and master key")
    cu = sub.add_parser("create-user", help="create a user (password prompted)")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=[r.value for r in Role], default="viewer")
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
    elif a.cmd == "serve":
        import uvicorn

        uvicorn.run("apex.api.main:create_app", factory=True, host=a.host, port=a.port, proxy_headers=True)


if __name__ == "__main__":
    main()
