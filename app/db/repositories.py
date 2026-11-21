# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""All DB reads/writes for tracked entities, audits, and service events.

Design rules:
- The only query that touches more than a handful of rows is `claim_due`, and it
  is bounded by `limit`.
- Every write goes through a session the caller controls (session.begin()), so
  batches are transactions.
- No caching of payloads at this layer.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, ServiceEvent, TrackedEntity

def now() -> datetime:
    return datetime.now(timezone.utc)

async def enqueue(
    session: AsyncSession,
    entity_type: str,
    tag: str,
    priority: int,
    ttl_days: int,
) -> bool:
    """Insert or bump a tracked entity. Returns True if it was newly inserted."""
    ts = now()
    stmt = pg_insert(TrackedEntity).values(
        entity_type=entity_type,
        tag=tag,
        priority=priority,
        priority_score=float(priority),
        next_poll_at=ts,
        tracking_started_at=ts,
        tracking_expires_at=ts + timedelta(days=ttl_days),
        version=0,
        window_started_at=ts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TrackedEntity.entity_type, TrackedEntity.tag],
        set_={
            "priority": stmt.excluded.priority,
            "priority_score": func.greatest(
                TrackedEntity.priority_score, stmt.excluded.priority_score
            ),
            "next_poll_at": func.least(
                TrackedEntity.next_poll_at, stmt.excluded.next_poll_at
            ),
            "tracking_expires_at": stmt.excluded.tracking_expires_at,
            "is_dead": False,
            "dead_since": None,
        },
    ).returning(TrackedEntity.tracking_started_at == ts)
    result = await session.execute(stmt)
    return bool(result.scalar_one_or_none())


async def revive(session: AsyncSession, entity_type: str, tag: str) -> bool:
    ts = now()
    result = await session.execute(
        update(TrackedEntity)
        .where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.tag == tag,
            TrackedEntity.is_dead.is_(True),
        )
        .values(
            is_dead=False,
            dead_since=None,
            next_poll_at=ts,
            last_error=None,
        )
    )
    return (result.rowcount or 0) > 0


async def mark_dead(
    session: AsyncSession,
    entity_type: str,
    tag: str,
    reason: str,
) -> None:
    ts = now()
    await session.execute(
        update(TrackedEntity)
        .where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.tag == tag,
        )
        .values(
            is_dead=True,
            dead_since=ts,
            next_poll_at=ts + timedelta(days=365 * 100),
            last_error=reason,
        )
    )

async def get_row(
    session: AsyncSession, entity_type: str, tag: str
) -> TrackedEntity | None:
    result = await session.execute(
        select(TrackedEntity).where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.tag == tag,
        )
    )
    return result.scalar_one_or_none()


async def get_snapshot(
    session: AsyncSession, entity_type: str, tag: str
) -> tuple[dict | None, bool]:
    """Return (payload, is_dead). payload is None if never polled."""
    result = await session.execute(
        select(TrackedEntity.payload, TrackedEntity.is_dead).where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.tag == tag,
        )
    )
    row = result.first()
    if row is None:
        return None, False
    return row.payload, row.is_dead


async def count_tracked(session: AsyncSession, entity_type: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(TrackedEntity)
        .where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.is_dead.is_(False),
        )
    )
    return int(result.scalar() or 0)


async def claim_due(
    session: AsyncSession,
    entity_type: str,
    limit: int,
    worker_id: str,
    stale_claim_seconds: int,
) -> list[TrackedEntity]:
    """Claim due entities using SKIP LOCKED.

    Safe with N concurrent workers across processes. Caller must be inside an
    open transaction (session.begin() or an already-active session) so the
    lock survives until commit/rollback.
    """
    ts = now()
    stale_before = ts - timedelta(seconds=stale_claim_seconds)
    # First: release stale claims so crashed workers don't starve rows.
    await session.execute(
        update(TrackedEntity)
        .where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.claimed_at.isnot(None),
            TrackedEntity.claimed_at < stale_before,
        )
        .values(claimed_by=None, claimed_at=None)
    )
    # Then: claim.
    result = await session.execute(
        select(TrackedEntity)
        .where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.is_dead.is_(False),
            TrackedEntity.next_poll_at <= ts,
            TrackedEntity.claimed_by.is_(None),
        )
        .order_by(
            TrackedEntity.priority_score.desc(),
            TrackedEntity.next_poll_at.asc(),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(result.scalars().all())
    if rows:
        ids = [(r.entity_type, r.tag) for r in rows]
        from sqlalchemy import tuple_  # local import keeps top clean
        await session.execute(
            update(TrackedEntity)
            .where(tuple_(TrackedEntity.entity_type, TrackedEntity.tag).in_(ids))
            .values(claimed_by=worker_id, claimed_at=ts)
        )
    return rows


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def update_after_poll(
    session: AsyncSession,
    entity_type: str,
    tag: str,
    *,
    payload: dict | None,
    payload_hash: str | None,
    next_poll_at: datetime,
    priority_score: float,
    access_count_24h: int,
    diff_count_window: int,
    poll_count_window: int,
    window_started_at: datetime,
    stable_polls: int,
) -> None:
    await session.execute(
        update(TrackedEntity)
        .where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.tag == tag,
        )
        .values(
            payload=payload,
            payload_hash=payload_hash,
            next_poll_at=next_poll_at,
            last_polled_at=now(),
            version=TrackedEntity.version + 1,
            priority_score=priority_score,
            access_count_24h=access_count_24h,
            diff_count_window=diff_count_window,
            poll_count_window=poll_count_window,
            window_started_at=window_started_at,
            stable_polls=stable_polls,
            claimed_by=None,
            claimed_at=None,
            last_error=None,
        )
    )


async def update_after_failure(
    session: AsyncSession,
    entity_type: str,
    tag: str,
    error: str,
    next_poll_at: datetime,
) -> None:
    await session.execute(
        update(TrackedEntity)
        .where(
            TrackedEntity.entity_type == entity_type,
            TrackedEntity.tag == tag,
        )
        .values(
            last_error=error,
            next_poll_at=next_poll_at,
            claimed_by=None,
            claimed_at=None,
        )
    )


async def record_access(
    session: AsyncSession,
    rows: list[tuple[str, str]],
) -> None:
    """Increment access counters for (entity_type, tag) pairs. Called on
    FastAPI hit, debounced by the caller into bulk updates."""
    if not rows:
        return
    from sqlalchemy import tuple_
    ts = now()
    await session.execute(
        update(TrackedEntity)
        .where(tuple_(TrackedEntity.entity_type, TrackedEntity.tag).in_(rows))
        .values(
            access_count_24h=TrackedEntity.access_count_24h + 1,
            last_accessed_at=ts,
        )
    )


async def bulk_write_audit(
    session: AsyncSession, rows: list[dict]
) -> None:
    if rows:
        await session.execute(pg_insert(AuditLog).values(rows))


async def write_service_event(
    session: AsyncSession,
    event_type: str,
    key_name: str | None,
    payload: dict | None,
) -> None:
    await session.execute(
        pg_insert(ServiceEvent).values(
            event_type=event_type,
            key_name=key_name,
            payload=payload,
            created_at=now(),
        )
    )

async def get_audit(
    session: AsyncSession,
    entity_type: str,
    tag: str,
    since: datetime | None,
    limit: int,
) -> list[AuditLog]:
    stmt = select(AuditLog).where(
        AuditLog.entity_type == entity_type,
        AuditLog.entity_tag == tag,
    )
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

async def purge_old_service_events(session: AsyncSession, retention_days: int) -> int:
    cutoff = now() - timedelta(days=retention_days)
    result = await session.execute(
        delete(ServiceEvent).where(ServiceEvent.created_at < cutoff)
    )
    return result.rowcount or 0