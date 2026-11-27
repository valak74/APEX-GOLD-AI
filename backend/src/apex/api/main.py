from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

import apex
from apex.api.routes import analysis, auth, backtests, brokers, research, risk, system
from apex.api.state import AppState, RateLimiter
from apex.broker.factory import build_broker
from apex.core.config import Settings, get_settings
from apex.core.logging import configure_logging, get_logger
from apex.db.session import make_engine, make_sessionmaker

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if len(settings.jwt_secret.get_secret_value()) < 32:
        raise RuntimeError("APEX_JWT_SECRET must be set (>= 32 chars). Generate one with `apex gen-keys`.")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        broker = build_broker(settings)  # refuses LIVE in this build
        await broker.connect()
        app.state.apex = AppState(
            settings=settings,
            engine=engine,
            sessionmaker=make_sessionmaker(engine),
            broker=broker,
            login_limiter=RateLimiter(settings.login_rate_limit_per_minute),
        )
        await app.state.apex.market.start(settings, app.state.apex.sessionmaker, broker)
        from apex.api.scanner import OpportunityScanner
        from apex.intel.explainer import Explainer

        explainer = Explainer(settings.llm_model, settings.llm_effort) if settings.llm_enabled else None
        app.state.apex.scanner = OpportunityScanner(app.state.apex, explainer)
        if settings.scanner_enabled:
            app.state.apex.scanner.start()
        from apex.api.risk_service import RiskService

        app.state.apex.risk = RiskService(app.state.apex)
        if settings.risk_guard_enabled:
            app.state.apex.risk.start()
        log.info(
            "startup",
            mode=broker.mode.value,
            broker=broker.capabilities.name,
            version=apex.__version__,
            market_data=settings.market_data_source,
        )
        try:
            yield
        finally:
            await app.state.apex.risk.stop()
            await app.state.apex.scanner.stop()
            await app.state.apex.market.stop()
            await broker.disconnect()
            await engine.dispose()

    app = FastAPI(
        title="APEX GOLD AI",
        version=apex.__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "prod" else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(brokers.router)
    app.include_router(analysis.router)
    app.include_router(risk.router)
    app.include_router(backtests.router)
    app.include_router(research.router)
    return app
