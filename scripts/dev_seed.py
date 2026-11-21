# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Seed the dev DB with a handful of fake entities for smoke testing.

Does NOT call the COC API. Uses only the DB layer. Run against the dev compose
stack:

    docker compose -f docker-compose.dev.yml run --rm app python -m scripts.dev_seed
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import load_config
from app.db import repositories as repo
from app.db.engine import make_engine, make_session_factory
from app.db.models import Base
from app.logging_setup import setup_logging


FAKE_CLANS = ["2PP", "8P2Y8Y8Q", "9Y0R9R0Q", "Y2LQ8GQ", "2Y0V8Y2"]
FAKE_PLAYERS = ["9UYV0LV", "YY2LQ8GQ", "2P0RVY8Y", "8YYQ2L0Y", "LCYUVLV"]


async def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    log = logging.getLogger("dev_seed")

    engine = make_engine(cfg)
    session_factory = make_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    ts = datetime.now(timezone.utc)
    async with session_factory() as session, session.begin():
        for tag in FAKE_CLANS:
            await repo.enqueue(session, "clan", tag, cfg.scheduled_priority, cfg.default_ttl_days)
            await repo.update_after_poll(
                session,
                "clan",
                tag,
                payload={
                    "tag": f"#{tag}",
                    "name": f"Seeded Clan {tag}",
                    "clanLevel": 10,
                    "members": 5,
                    "memberList": [],
                },
                payload_hash="seed",
                next_poll_at=ts + timedelta(seconds=300),
                priority_score=1.0,
                access_count_24h=0,
                diff_count_window=0,
                poll_count_window=0,
                window_started_at=ts,
                stable_polls=0,
            )
        for tag in FAKE_PLAYERS:
            await repo.enqueue(session, "player", tag, cfg.scheduled_priority, cfg.default_ttl_days)
            await repo.update_after_poll(
                session,
                "player",
                tag,
                payload={
                    "tag": f"#{tag}",
                    "name": f"Seeded Player {tag}",
                    "expLevel": 100,
                    "trophies": 3000,
                },
                payload_hash="seed",
                next_poll_at=ts + timedelta(seconds=60),
                priority_score=1.0,
                access_count_24h=0,
                diff_count_window=0,
                poll_count_window=0,
                window_started_at=ts,
                stable_polls=0,
            )
    log.info("seeded %d clans, %d players", len(FAKE_CLANS), len(FAKE_PLAYERS))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())