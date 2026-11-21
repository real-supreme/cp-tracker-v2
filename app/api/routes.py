# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_client
from app.api.schemas import (
    AuditEntry,
    EnqueuedResponse,
    HealthResponse,
    LaneStats,
    ReviveResponse,
    StatsResponse,
)
from app.db import repositories as repo

router = APIRouter(prefix="/api/v1")

TAG_RE = re.compile(r"^[0-9A-NP-Za-np-z]{3,16}$")
ENTITY_TYPES = {"clan", "player", "war"}


def normalize_tag(tag: str) -> str:
    t = tag.strip().upper().lstrip("#")
    if not TAG_RE.match(t):
        raise HTTPException(status_code=400, detail="invalid tag")
    return t


async def get_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


async def _resolve(
    session: AsyncSession,
    request: Request,
    response: Response,
    entity_type: str,
    tag: str,
):
    tag = normalize_tag(tag)
    
    # 1. Check if the entity exists in the DB
    payload, is_dead = await repo.get_snapshot(session, entity_type, tag)
    if is_dead:
        raise HTTPException(status_code=410, detail="entity is marked dead")
    if payload is not None:
        return payload
        
    # 2. If not, add it to the tracking queue.
    # We use session.commit() here because get_snapshot already started a transaction.
    cfg = request.app.state.cfg
    await repo.enqueue(
        session, entity_type, tag, cfg.fastapi_priority, cfg.default_ttl_days
    )
    await session.commit()
    
    response.status_code = 202
    return EnqueuedResponse(entity_type=entity_type, tag=tag).model_dump()


@router.get("/health", response_model=HealthResponse, include_in_schema=True)
async def health(request: Request):
    return HealthResponse(status="ok", paused=request.app.state.throttler.is_paused())


@router.get("/stats", response_model=StatsResponse)
async def stats(
    request: Request,
    _client: str = Depends(require_client),
    session: AsyncSession = Depends(get_session),
):
    cap = request.app.state.capacity
    throttler = request.app.state.throttler
    lanes = {}
    for name in ("clan", "player", "war"):
        tracked = await repo.count_tracked(session, name)
        lane_cap = cap.lanes[name]
        lanes[name] = LaneStats(
            tracked=tracked,
            e_max=lane_cap.e_max,
            workers=lane_cap.workers,
            batch_size=lane_cap.batch_size,
            rps=lane_cap.lane_rps,
        )
    return StatsResponse(
        paused=throttler.is_paused(),
        keys_configured=throttler.key_count(),
        keys_throttled=throttler.throttled_keys(),
        lanes=lanes,
    )


@router.get("/clans/{tag}")
async def get_clan(
    tag: str,
    request: Request,
    response: Response,
    _client: str = Depends(require_client),
    session: AsyncSession = Depends(get_session),
):
    return await _resolve(session, request, response, "clan", tag)


@router.get("/players/{tag}")
async def get_player(
    tag: str,
    request: Request,
    response: Response,
    _client: str = Depends(require_client),
    session: AsyncSession = Depends(get_session),
):
    return await _resolve(session, request, response, "player", tag)


@router.get("/wars/{tag}")
async def get_war(
    tag: str,
    request: Request,
    response: Response,
    _client: str = Depends(require_client),
    session: AsyncSession = Depends(get_session),
):
    return await _resolve(session, request, response, "war", tag)


@router.get("/audit/{entity_type}/{tag}", response_model=list[AuditEntry])
async def get_audit(
    entity_type: str,
    tag: str,
    since: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    _client: str = Depends(require_client),
    session: AsyncSession = Depends(get_session),
):
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="bad entity_type")
    rows = await repo.get_audit(session, entity_type, normalize_tag(tag), since, limit)
    return [AuditEntry.model_validate(r) for r in rows]


@router.post("/{entity_type}/{tag}/revive", response_model=ReviveResponse)
async def revive(
    entity_type: str,
    tag: str,
    _client: str = Depends(require_client),
    session: AsyncSession = Depends(get_session),
):
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="bad entity_type")
    tag = normalize_tag(tag)
    
    # No session.begin() here either. Just run the update and commit.
    ok = await repo.revive(session, entity_type, tag)
    await session.commit()
    
    return ReviveResponse(revived=ok, entity_type=entity_type, tag=tag)