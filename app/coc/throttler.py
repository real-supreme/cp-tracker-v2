# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import time
from collections import deque


class _Bucket:
    __slots__ = ("key", "rps", "tokens", "last_refill", "muted_until")

    def __init__(self, key: str, rps: float) -> None:
        self.key = key
        self.rps = rps
        self.tokens = float(rps)
        self.last_refill = time.monotonic()
        self.muted_until = 0.0

    def _refill(self) -> None:
        t = time.monotonic()
        self.tokens = min(self.rps, self.tokens + (t - self.last_refill) * self.rps)
        self.last_refill = t

    def try_consume(self) -> bool:
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def wait_seconds(self) -> float:
        self._refill()
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.rps

    def is_available(self, now: float) -> bool:
        return now >= self.muted_until


class AsyncThrottler:
    def __init__(
        self,
        keys: list[str],
        rps_per_key: float,
        throttle_retry_seconds: int = 60,
    ) -> None:
        if not keys:
            raise ValueError("AsyncThrottler requires at least one key")
        self._rps_per_key = rps_per_key
        self._buckets: deque[_Bucket] = deque(_Bucket(k, rps_per_key) for k in keys)
        self._buckets_by_key = {b.key: b for b in self._buckets}
        self._lock = asyncio.Lock()
        self._throttle_retry = throttle_retry_seconds
        self._paused_until = 0.0
        self._requests_sent = 0

    def is_paused(self) -> bool:
        return time.monotonic() < self._paused_until

    def requests_sent(self) -> int:
        return self._requests_sent

    def key_count(self) -> int:
        return len(self._buckets)

    def throttled_keys(self) -> list[str]:
        t = time.monotonic()
        return [b.key for b in self._buckets if b.muted_until > t]

    async def pause(self, seconds: int) -> None:
        async with self._lock:
            self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    async def unpause(self) -> None:
        async with self._lock:
            self._paused_until = 0.0

    async def mark_throttled(self, key: str) -> None:
        async with self._lock:
            b = self._buckets_by_key.get(key)
            if b is not None:
                b.muted_until = time.monotonic() + self._throttle_retry

    async def halve_key(self, key: str) -> None:
        async with self._lock:
            b = self._buckets_by_key.get(key)
            if b is not None:
                b.rps = max(1.0, b.rps / 2.0)

    async def reset_keys(self, keys: list[str]) -> None:
        """Replace all keys in the throttler. Called by the client after
        refreshing keys from the developer site."""
        if not keys:
            raise ValueError("reset_keys requires at least one key")
        async with self._lock:
            self._buckets = deque(_Bucket(k, self._rps_per_key) for k in keys)
            self._buckets_by_key = {b.key: b for b in self._buckets}

    async def acquire(self) -> str:
        while True:
            async with self._lock:
                t = time.monotonic()
                if t < self._paused_until:
                    wait = max(0.05, self._paused_until - t)
                else:
                    n = len(self._buckets)
                    chosen: str | None = None
                    soonest = float("inf")
                    for _ in range(n):
                        b = self._buckets.popleft()
                        if b.is_available(t) and b.try_consume():
                            chosen = b.key
                            self._buckets.append(b)
                            break
                        if b.is_available(t):
                            soonest = min(soonest, b.wait_seconds())
                        else:
                            soonest = min(soonest, b.muted_until - t)
                        self._buckets.append(b)
                    if chosen is not None:
                        self._requests_sent += 1
                        return chosen
                    wait = max(0.05, soonest if soonest < float("inf") else 0.5)
            await asyncio.sleep(wait)