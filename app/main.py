# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import ClientRateLimiter
from app.api.routes import router
from app.coc.alerts import AlertService
from app.coc.capacity import compute_capacity, format_capacity_report
from app.coc.client import COCClient
from app.coc.poller import Poller
from app.coc.throttler import AsyncThrottler
from app.config import load_config
from app.db import repositories as repo
from app.db.engine import make_engine, make_session_factory
from app.db.models import Base
from app.logging_setup import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    setup_logging(cfg.log_level)

    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    throttler = AsyncThrottler(
        cfg.api_keys,
        rps_per_key=cfg.requests_per_second_per_key * cfg.effective_rate_pct_of_max,
        throttle_retry_seconds=cfg.throttle_retry_seconds,
    )
    client = COCClient(cfg, throttler)
    if not await client.initialise_keys() and not cfg.api_keys:
        logger.error(
            "No API keys available. Please provide valid keys in the config or "
            "provide developer credentials to auto-generate keys."
        )
        sys.exit(1)
        return  # --- IGNORE ---
    await client.start_selfcheck()

    capacity = compute_capacity(cfg)
    for line in format_capacity_report(capacity, cfg).splitlines():
        logger.info(line)

    alerts = AlertService(cfg.smtp, cfg.email_cooldown_seconds)

    # Seed init entities before starting the poller.
    async with session_factory() as session, session.begin():
        for tag in cfg.init_clans:
            await repo.enqueue(
                session, "clan", tag, cfg.scheduled_priority, cfg.default_ttl_days
            )
        for tag in cfg.init_players:
            await repo.enqueue(
                session, "player", tag, cfg.scheduled_priority, cfg.default_ttl_days
            )

    poller = Poller(cfg, capacity, client, throttler, session_factory, alerts)
    await poller.start()

    app.state.cfg = cfg
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.throttler = throttler
    app.state.client = client
    app.state.poller = poller
    app.state.capacity = capacity
    app.state.client_keys = cfg.api_client_keys
    app.state.client_limiter = ClientRateLimiter(cfg.client_rpm_limit)

    logger.info(
        "tracker started: keys=%d clients=%d lanes=3",
        len(cfg.api_keys), len(cfg.api_client_keys),
    )
    try:
        yield
    finally:
        logger.info("shutting down")
        await poller.stop()
        await client.aclose()
        await engine.dispose()
        logger.info("tracker stopped")


def create_app() -> FastAPI:
    try:
        cfg = load_config()
    except Exception as e:
        print(f"FATAL: config load failed: {e}", file=sys.stderr)
        raise

    app = FastAPI(title="Clash Panda Tracker", version="0.2.0", lifespan=lifespan)
    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(router)
    return app


app = create_app()