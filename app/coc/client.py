# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Shared async HTTP client for the COC API. Used by both the poller and by
FastAPI on-demand requests, so on-demand reads are automatically throttled
against the same token bucket that the poller uses.

Rate limit self-check: every `selfcheck_window` seconds, compare actual
requests sent to the expected count. If materially over, the throttler halves
the offending keys — this is what catches a bug that would otherwise silently
burn through keys.
"""
import asyncio
import base64
import datetime
import json
import logging
import time

import httpx

from app.coc.throttler import AsyncThrottler
from app.config import Config

logger = logging.getLogger(__name__)


class COCError(Exception): ...
class ServiceUnavailable(COCError): ...
class InvalidCredentials(COCError): ...
class Throttled(COCError): ...
class NotFound(COCError): ...
class APIError(COCError): ...


def _parse_max_age(cache_control: str) -> int:
    if not cache_control:
        return 0
    for part in cache_control.split(","):
        p = part.strip()
        if p.lower().startswith("max-age="):
            try:
                return int(p.split("=", 1)[1].strip())
            except ValueError:
                return 0
    return 0


class COCClient:
    SELF_CHECK_WINDOW = 10.0
    SELF_CHECK_TOLERANCE = 1.05
    DEV_SITE_URL = "https://developer.clashofclans.com/api"
    MAX_KEYS = 10

    def __init__(self, cfg: Config, throttler: AsyncThrottler) -> None:
        self._cfg = cfg
        self._throttler = throttler
        self._http = httpx.AsyncClient(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(cfg.request_timeout_seconds),
            headers={"Accept": "application/json"},
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        self._sem = asyncio.Semaphore(cfg.max_concurrent_requests or 200)
        self._selfcheck_task: asyncio.Task | None = None
        self._selfcheck_start = time.monotonic()
        self._selfcheck_baseline = 0

    async def start_selfcheck(self) -> None:
        self._selfcheck_start = time.monotonic()
        self._selfcheck_baseline = self._throttler.requests_sent()
        self._selfcheck_task = asyncio.create_task(self._selfcheck_loop())

    async def _selfcheck_loop(self) -> None:
        expected_rps = (
            self._throttler.key_count()
            * self._cfg.requests_per_second_per_key
            * self._cfg.effective_rate_pct_of_max
        )
        while True:
            await asyncio.sleep(self.SELF_CHECK_WINDOW)
            now = time.monotonic()
            elapsed = now - self._selfcheck_start
            if elapsed <= 0:
                continue
            sent = self._throttler.requests_sent() - self._selfcheck_baseline
            actual_rps = sent / elapsed
            if actual_rps > expected_rps * self.SELF_CHECK_TOLERANCE:
                logger.error(
                    "rate self-check: actual=%.1f rps > expected=%.1f rps — halving all keys",
                    actual_rps, expected_rps,
                )
                for k in list(self._throttler._buckets_by_key.keys()):
                    await self._throttler.halve_key(k)
            self._selfcheck_start = now
            self._selfcheck_baseline = self._throttler.requests_sent()

    async def aclose(self) -> None:
        if self._selfcheck_task:
            self._selfcheck_task.cancel()
            try:
                await self._selfcheck_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._http.aclose()

    async def _get(self, path: str) -> tuple[dict, int]:
        last_exc: Exception | None = None
        for attempt in range(self._cfg.max_retries + 1):
            if self._throttler.is_paused():
                raise ServiceUnavailable("throttler paused")
            key = await self._throttler.acquire()
            async with self._sem:
                try:
                    resp = await self._http.get(
                        path, headers={"Authorization": f"Bearer {key}"}
                    )
                except (httpx.TimeoutException, httpx.HTTPError) as e:
                    last_exc = e
                    if attempt >= self._cfg.max_retries:
                        raise APIError(f"network: {e}") from e
                    await asyncio.sleep(min(2 ** attempt, 8))
                    continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except json.JSONDecodeError as e:
                    last_exc = e
                    if attempt >= self._cfg.max_retries:
                        raise APIError(f"invalid JSON: {e}") from e
                    await asyncio.sleep(min(2 ** attempt, 8))
                    continue
                return data, _parse_max_age(resp.headers.get("Cache-Control", ""))

            if resp.status_code == 404:
                raise NotFound(path)

            if resp.status_code == 403:
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    data = {}
                logger.warning("403 forbidden: %s — %s", path, data)
                if (
                    isinstance(data, dict)
                    and data.get("reason") == "accessDenied.invalidIp"
                    and self._cfg.coc_email
                    and self._cfg.coc_password
                ):
                    logger.info("invalid IP detected — refreshing keys")
                    await self.initialise_keys()
                raise APIError(f"403 forbidden: {path}")

            if resp.status_code == 429:
                await self._throttler.mark_throttled(key)
                if self._cfg.rpm_halve_enabled:
                    await self._throttler.halve_key(key)
                if attempt >= self._cfg.max_retries:
                    raise Throttled(path)
                await asyncio.sleep(self._cfg.throttle_retry_seconds)
                continue

            if resp.status_code == 503:
                await self._throttler.pause(self._cfg.service_unavailable_pause_seconds)
                raise ServiceUnavailable(path)

            if resp.status_code >= 500:
                last_exc = APIError(f"{resp.status_code} {path}")
                if attempt >= self._cfg.max_retries:
                    raise last_exc
                await asyncio.sleep(min(2 ** attempt, 8))
                continue

            raise APIError(f"{resp.status_code} {path}")
        raise APIError(f"exhausted retries: {last_exc}")

    async def get_clan(self, tag: str) -> tuple[dict, int]:
        return await self._get(f"/clans/%23{tag.lstrip('#')}")

    async def get_player(self, tag: str) -> tuple[dict, int]:
        return await self._get(f"/players/%23{tag.lstrip('#')}")

    async def get_current_war(self, tag: str) -> tuple[dict, int]:
        return await self._get(f"/clans/%23{tag.lstrip('#')}/currentwar")

    async def initialise_keys(self) -> bool:
        """Logs into the developer site, revokes dead keys, creates missing ones,
        and replaces the throttler's keys with the working set."""
        if not self._cfg.coc_email or not self._cfg.coc_password:
            logger.info("No developer credentials provided. Skipping auto-key management.")
            return False

        logger.info("Refreshing keys from the developer site.")
        async with httpx.AsyncClient(base_url=self.DEV_SITE_URL, timeout=15.0) as session:
            resp = await session.post(
                "/login",
                json={"email": self._cfg.coc_email, "password": self._cfg.coc_password},
            )
            if resp.status_code != 200:
                logger.error("Login failed: HTTP %s", resp.status_code)
                raise InvalidCredentials("Invalid COC developer credentials")

            payload = resp.json()
            token = payload.get("temporaryAPIToken", "")
            if not token:
                raise InvalidCredentials("Login response missing temporaryAPIToken")

            try:
                jwt_payload = token.split(".")[1]
                jwt_payload += "=" * (-len(jwt_payload) % 4)
                decoded = json.loads(base64.b64decode(jwt_payload))
                ip = decoded["limits"][1]["cidrs"][0].split("/")[0]
            except (IndexError, KeyError, ValueError) as e:
                raise APIError(f"Could not decode developer token: {e}") from e

            logger.info("Current external IP: %s", ip)

            resp = await session.post("/apikey/list")
            all_keys = resp.json().get("keys", [])

            # 1. Identify working keys (matching current IP)
            working_keys = [k["key"] for k in all_keys if ip in k.get("cidrRanges", [])]
            logger.info("Found %d working keys for IP %s", len(working_keys), ip)

            wanted = min(len(self._cfg.api_keys), self.MAX_KEYS)

            # 2. If we don't have enough, revoke dead keys to make room
            if len(working_keys) < wanted:
                logger.info("Not enough working keys. Purging dead keys to make room...")
                
                for key in all_keys[:]:  # iterate copy
                    # Only delete keys that match our name AND are bound to a different IP
                    if key.get("name") == self._cfg.coc_key_name and ip not in key.get("cidrRanges", []):
                        logger.info("Revoking dead key (ID: %s) bound to %s", key["id"], key["cidrRanges"])
                        await session.post("/apikey/revoke", json={"id": key["id"]})
                        all_keys.remove(key)

                # 3. Create missing keys
                missing = wanted - len(working_keys)
                logger.info("Creating %d new keys (have %d, want %d)", missing, len(working_keys), wanted)
                
                for _ in range(missing):
                    data = {
                        "name": self._cfg.coc_key_name,
                        "description": f"Created {datetime.datetime.now().isoformat()}",
                        "cidrRanges": [ip],
                        "scopes": ["clash"],
                    }
                    resp = await session.post("/apikey/create", json=data)
                    if resp.status_code != 200:
                        body = resp.json()
                        raise ValueError(body.get("description", str(body)))
                    working_keys.append(resp.json()["key"]["key"])

            if not working_keys:
                raise RuntimeError("No usable API keys after refresh")

            # Trim to what we asked for
            working_keys = working_keys[:wanted]

            # Replace the throttler's keys with the fresh set
            await self._throttler.reset_keys(working_keys)

            logger.info("Throttler now holds %d working keys", len(working_keys))
            return True