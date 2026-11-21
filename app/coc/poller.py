# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""The poller.

Structure:
  - One `Lane` per entity_type. A lane spawns N worker tasks (auto or config).
  - Each worker: claim a batch (SKIP LOCKED) -> fire -> diff -> write -> release.
  - Batched writes: workers funnel their results into a small buffer; when
    the buffer hits `write_batch_size`, it is flushed in one transaction.
  - On pause (503): probe with 1 entity per lane at the pause interval.
  - War rows: created when a clan poll sees a war; marked dead on ENDED /
    NOT_IN_WAR. CWL cooldown is enforced by `next_poll_at` at write time.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.coc.alerts import AlertService
from app.coc.capacity import Capacity
from app.coc.client import (
    APIError,
    COCClient,
    NotFound,
    ServiceUnavailable,
    Throttled,
)
from app.coc.diff import (
    diff,
    fingerprint,
    is_cwl_war,
    next_war_poll_seconds,
    war_is_ended,
    war_is_trackable,
)
from app.coc.priority import (
    apply_cache_floor,
    compute_interval_seconds,
    compute_priority_score,
    roll_window,
)
from app.coc.throttler import AsyncThrottler
from app.config import Config
from app.db import repositories as repo

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_crdb_retry(e: Exception) -> bool:
    """Check if an exception is a CockroachDB serialization retry."""
    msg = str(e).lower()
    return "retry_serializable" in msg or "restart transaction" in msg


class _WriteBuffer:
    """Buffers per-poll results so we can flush in batched transactions."""
    def __init__(self, batch_size: int, session_factory: async_sessionmaker) -> None:
        self._batch = batch_size
        self._sf = session_factory
        self._updates: list[dict] = []
        self._audits: list[dict] = []
        self._failures: list[dict] = []
        self._lock = asyncio.Lock()

    async def push_update(self, payload: dict) -> None:
        async with self._lock:
            self._updates.append(payload)
            await self._maybe_flush_locked()

    async def push_audit(self, event: dict) -> None:
        async with self._lock:
            self._audits.append(event)
            await self._maybe_flush_locked()

    async def push_failure(self, payload: dict) -> None:
        async with self._lock:
            self._failures.append(payload)
            await self._maybe_flush_locked()

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _maybe_flush_locked(self) -> None:
        if (
            len(self._updates) >= self._batch
            or len(self._audits) >= self._batch
            or len(self._failures) >= self._batch
        ):
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        updates, audits, failures = self._updates, self._audits, self._failures
        self._updates, self._audits, self._failures = [], [], []
        if not (updates or audits or failures):
            return
            
        for attempt in range(3):
            try:
                async with self._sf() as session, session.begin():
                    for f in failures:
                        await repo.update_after_failure(
                            session, f["entity_type"], f["tag"],
                            f["error"], f["next_poll_at"],
                        )
                    for u in updates:
                        await repo.update_after_poll(
                            session,
                            u["entity_type"],
                            u["tag"],
                            payload=u["payload"],
                            payload_hash=u["payload_hash"],
                            next_poll_at=u["next_poll_at"],
                            priority_score=u["priority_score"],
                            access_count_24h=u["access_count_24h"],
                            diff_count_window=u["diff_count_window"],
                            poll_count_window=u["poll_count_window"],
                            window_started_at=u["window_started_at"],
                            stable_polls=u["stable_polls"],
                        )
                    await repo.bulk_write_audit(session, audits)
                return # Success
            except DBAPIError as e:
                if _is_crdb_retry(e) and attempt < 2:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.exception("write buffer flush failed (%d updates, %d audits)",
                                 len(updates), len(audits))
                break
            except Exception:
                logger.exception("write buffer flush failed (%d updates, %d audits)",
                                 len(updates), len(audits))
                break


class Lane:
    def __init__(
        self,
        name: str,
        cfg: Config,
        capacity_lane,
        client: COCClient,
        throttler: AsyncThrottler,
        session_factory: async_sessionmaker,
        alerts: AlertService,
    ) -> None:
        self.name = name
        self.cfg = cfg
        self.cap = capacity_lane
        self.client = client
        self.throttler = throttler
        self.sf = session_factory
        self.alerts = alerts
        self._workers: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._buffer = _WriteBuffer(cfg.write_batch_size, session_factory)

        if cfg.auto_workers:
            self.worker_count = self.cap.workers
            self.batch_size = self.cap.batch_size
        else:
            override = getattr(cfg, f"{name}_workers")
            self.worker_count = max(1, int(override or 1))
            self.batch_size = max(4, int(self.cap.batch_size))

    async def start(self) -> None:
        for i in range(self.worker_count):
            self._workers.append(asyncio.create_task(
                self._worker(f"{self.name}-{i}"),
                name=f"worker-{self.name}-{i}",
            ))
        logger.info(
            "lane %s started: workers=%d batch=%d e_max=%d rps=%.1f",
            self.name, self.worker_count, self.batch_size,
            self.cap.e_max, self.cap.lane_rps,
        )

    async def stop(self) -> None:
        self._stop.set()
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        await self._buffer.flush()

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _lane_cfg(self):
        return getattr(self.cfg, self.name)

    async def _fetch(self, tag: str) -> tuple[dict, int]:
        if self.name == "clan":
            return await self.client.get_clan(tag)
        if self.name == "player":
            return await self.client.get_player(tag)
        if self.name == "war":
            return await self.client.get_current_war(tag)
        raise ValueError(self.name)

    async def _worker(self, worker_id: str) -> None:
        while not self._stop.is_set():
            try:
                if self.throttler.is_paused():
                    await self._probe_when_paused(worker_id)
                    continue

                # --- RETRY LOOP FOR COCKROACH SERIALIZATION ERRORS ---
                rows = []
                for attempt in range(3):
                    try:
                        async with self.sf() as session, session.begin():
                            rows = await repo.claim_due(
                                session, self.name, self.batch_size,
                                worker_id, self.cfg.stale_claim_seconds,
                            )
                        break # Success! Break out of the retry loop
                    except DBAPIError as e:
                        if _is_crdb_retry(e) and attempt < 2:
                            await asyncio.sleep(0.1 * (attempt + 1))
                            continue
                        raise # If it's not a serialization error, crash normally
                # ---------------------------------------------------------

                if not rows:
                    await self._sleep_or_stop(3)
                    continue

                for row in rows:
                    if self._stop.is_set():
                        return
                    if self.throttler.is_paused():
                        break
                    await self._poll_one(row)
                    await asyncio.sleep(0)

                await self._buffer.flush()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("worker %s error", worker_id)
                await self._sleep_or_stop(10)

    async def _poll_one(self, row) -> None:
        try:
            new_data, max_age = await self._fetch(row.tag)
        except NotFound:
            await self._mark_not_found(row)
            return
        except (Throttled, ServiceUnavailable):
            return
        except APIError as e:
            await self._buffer.push_failure({
                "entity_type": self.name,
                "tag": row.tag,
                "error": str(e)[:500],
                "next_poll_at": _now() + timedelta(seconds=self._lane_cfg().min_interval_seconds),
            })
            return

        await self._handle_success(row, new_data, max_age)

    async def _handle_success(self, row, new_data: dict, cache_max_age: int) -> None:
        old = row.payload
        new_hash = fingerprint(new_data)
        unchanged = (new_hash is not None and new_hash == row.payload_hash)
        events = [] if unchanged else diff(self.name, old, new_data)

        # War-specific: mark dead once state is ENDED / NOT_IN_WAR, and apply
        # CWL cooldown by pushing next_poll_at out. The row remains dead.
        is_war_dead = False
        if self.name == "war":
            state = (new_data or {}).get("state", "")
            if war_is_ended(state):
                is_war_dead = True
            elif not war_is_trackable(state):
                # Weird intermediate — keep it but push out.
                pass

        if is_war_dead:
            await self._mark_war_dead(row, state=(new_data or {}).get("state", "ENDED"), events=events)
            return

        # Priority + interval
        diff_count, poll_count, access, window_start = roll_window(
            row.window_started_at,
            row.diff_count_window,
            row.poll_count_window,
            row.access_count_24h,
            self.cfg.priority,
        )
        poll_count += 1
        if events:
            diff_count += 1
            stable_polls = 0
        else:
            stable_polls = row.stable_polls + 1

        lane_cfg = self._lane_cfg()
        if self.name == "war":
            interval = next_war_poll_seconds(new_data)
            if is_cwl_war(new_data):
                interval = max(interval, 600)  # CWL: 10 min floor from your spec
        else:
            interval = compute_interval_seconds(lane_cfg, self.cfg.adaptive, stable_polls)
        interval = apply_cache_floor(interval, cache_max_age, lane_cfg.max_interval_seconds)

        score = compute_priority_score(access, diff_count, poll_count, self.cfg.priority)

        await self._buffer.push_update({
            "entity_type": self.name,
            "tag": row.tag,
            "payload": new_data,
            "payload_hash": new_hash,
            "next_poll_at": _now() + timedelta(seconds=interval),
            "priority_score": score,
            "access_count_24h": access,
            "diff_count_window": diff_count,
            "poll_count_window": poll_count,
            "window_started_at": window_start,
            "stable_polls": stable_polls,
        })

        if events:
            for ev in events:
                await self._buffer.push_audit({
                    "entity_type": self.name,
                    "entity_tag": row.tag,
                    "event_type": ev["event_type"],
                    "payload": ev["data"],
                    "created_at": _now(),
                })

    async def _mark_not_found(self, row) -> None:
        for attempt in range(3):
            try:
                async with self.sf() as session, session.begin():
                    await repo.mark_dead(session, self.name, row.tag, "not_found")
                await self._buffer.push_audit({
                    "entity_type": self.name,
                    "entity_tag": row.tag,
                    "event_type": "entity_died",
                    "payload": {"reason": "not_found"},
                    "created_at": _now(),
                })
                return
            except DBAPIError as e:
                if _is_crdb_retry(e) and attempt < 2:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.exception("mark_not_found failed for %s", row.tag)

    async def _mark_war_dead(self, row, state: str, events: list[dict] | None = None) -> None:
        for attempt in range(3):
            try:
                async with self.sf() as session, session.begin():
                    await repo.mark_dead(session, self.name, row.tag, f"war_state:{state}")
                await self._buffer.push_audit({
                    "entity_type": self.name,
                    "entity_tag": row.tag,
                    "event_type": "war_ended",
                    "payload": {"state": state},
                    "created_at": _now(),
                })
                if events:
                    for ev in events:
                        await self._buffer.push_audit({
                            "entity_type": self.name,
                            "entity_tag": row.tag,
                            "event_type": ev["event_type"],
                            "payload": ev["data"],
                            "created_at": _now(),
                        })
                return
            except DBAPIError as e:
                if _is_crdb_retry(e) and attempt < 2:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.exception("mark_war_dead failed for %s", row.tag)

    async def _probe_when_paused(self, worker_id: str) -> None:
        wait = self.cfg.service_unavailable_pause_seconds
        await self._sleep_or_stop(wait)
        if not self.throttler.is_paused():
            return

        rows = []
        for attempt in range(3):
            try:
                async with self.sf() as session, session.begin():
                    rows = await repo.claim_due(
                        session, self.name, 1, worker_id, self.cfg.stale_claim_seconds,
                    )
                break
            except DBAPIError as e:
                if _is_crdb_retry(e) and attempt < 2:
                    await asyncio.sleep(0.5)
                    continue
                return

        if not rows:
            await self._sleep_or_stop(60)
            return

        row = rows[0]
        try:
            new_data, max_age = await self._fetch(row.tag)
        except ServiceUnavailable:
            await self.throttler.pause(self.cfg.service_unavailable_pause_seconds)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("probe error on %s: %s", self.name, e)
            return

        await self._handle_success(row, new_data, max_age)
        await self._buffer.flush()
        await self.throttler.unpause()
        logger.info("service recovered — lane %s resumed", self.name)


class Poller:
    def __init__(
        self,
        cfg: Config,
        capacity: Capacity,
        client: COCClient,
        throttler: AsyncThrottler,
        session_factory: async_sessionmaker,
        alerts: AlertService,
    ) -> None:
        self.cfg = cfg
        self.capacity = capacity
        self.client = client
        self.throttler = throttler
        self.sf = session_factory
        self.alerts = alerts
        self._lanes: dict[str, Lane] = {}
        self._cleanup_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        for name in ("clan", "player", "war"):
            self._lanes[name] = Lane(
                name, self.cfg, self.capacity.lanes[name],
                self.client, self.throttler, self.sf, self.alerts,
            )
            await self._lanes[name].start()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="cleanup")

    async def stop(self) -> None:
        self._stop.set()
        for lane in self._lanes.values():
            await lane.stop()
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for attempt in range(3):
                    try:
                        async with self.sf() as session, session.begin():
                            if purged_events:=(await repo.purge_old_service_events(session, retention_days=30)):
                                logger.info("cleanup: dead=%d audit=%d events=%d",purged_events)
                        break
                    except DBAPIError as e:
                        if _is_crdb_retry(e) and attempt < 2:
                            await asyncio.sleep(1.0)
                            continue
                        raise
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("cleanup error")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.cfg.cleanup_interval_seconds
                )
            except asyncio.TimeoutError:
                pass