# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Bearer auth + per-client token bucket.

Auth: `Authorization: Bearer <secret>`. The secret is looked up in
`app.state.client_keys` (a dict of {secret: client_name}) populated at startup
from `API_CLIENT_KEYS` in `.env`.

Rate limit: in-memory token bucket per client. `client_rpm_limit` per minute,
refilled continuously. Exceeding returns 429 with `Retry-After`.
"""
import asyncio
import time
from collections import defaultdict

from fastapi import Header, HTTPException, Request


class _ClientBucket:
    __slots__ = ("rpm", "tokens", "last_refill")

    def __init__(self, rpm: int) -> None:
        self.rpm = rpm
        self.tokens = float(rpm)
        self.last_refill = time.monotonic()

    def try_consume(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.rpm, self.tokens + (now - self.last_refill) * self.rpm / 60.0)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def wait_seconds(self) -> float:
        return max(0.0, (1.0 - self.tokens) / (self.rpm / 60.0))


class ClientRateLimiter:
    def __init__(self, rpm_limit: int) -> None:
        self._rpm = rpm_limit
        self._buckets: dict[str, _ClientBucket] = defaultdict(
            lambda: _ClientBucket(self._rpm)
        )
        self._lock = asyncio.Lock()

    async def check(self, client_name: str) -> float:
        async with self._lock:
            b = self._buckets[client_name]
            if b.try_consume():
                return 0.0
            return b.wait_seconds()


def _extract_secret(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    return authorization[7:].strip()


async def require_client(
    request: Request,
    authorization: str | None = Header(None),
) -> str:
    secret = _extract_secret(authorization)
    client_keys: dict[str, str] = request.app.state.client_keys
    client_name = client_keys.get(secret)
    if client_name is None:
        raise HTTPException(403, "invalid token")
    request.state.client_name = client_name
    limiter: ClientRateLimiter = request.app.state.client_limiter
    wait = await limiter.check(client_name)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail="client rate limit exceeded",
            headers={"Retry-After": f"{wait:.1f}"},
        )
    return client_name