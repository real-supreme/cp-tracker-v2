# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import ssl

from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Config


# --- CockroachDB Patch ---
# CockroachDB returns "CockroachDB CCL v23..." for `SELECT version()`, which
# crashes SQLAlchemy's Postgres version parser. We patch the asyncpg dialect
# to bypass the check and report PostgreSQL 14.0.0 (which CockroachDB is
# highly compatible with).
def _patch_cockroach_version(self, connection):
    return (14, 0, 0)

PGDialect_asyncpg._get_server_version_info = _patch_cockroach_version
# -------------------------


def make_engine(cfg: Config) -> AsyncEngine:
    args = {}
    if cfg.ssl_root_cert:
        ctx = ssl.create_default_context(cafile=cfg.ssl_root_cert)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        args["ssl"] = ctx

    return create_async_engine(
        cfg.database_url,
        pool_size=20,             # Increased from 10
        max_overflow=40,          # Increased from 5. Total 60 connections max.
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=False,
        connect_args=args,
        pool_timeout=30, 
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)