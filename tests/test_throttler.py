# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import pytest

from app.coc.throttler import AsyncThrottler


@pytest.mark.asyncio
async def test_acquire_returns_a_key():
    t = AsyncThrottler(["k1", "k2"], rps_per_key=1000)
    key = await t.acquire()
    assert key in ("k1", "k2")


@pytest.mark.asyncio
async def test_rotates_between_keys():
    t = AsyncThrottler(["k1", "k2"], rps_per_key=1000)
    seen = set()
    for _ in range(10):
        seen.add(await t.acquire())
    assert seen == {"k1", "k2"}


@pytest.mark.asyncio
async def test_paused_blocks():
    t = AsyncThrottler(["k1"], rps_per_key=1000)
    await t.pause(1)
    assert t.is_paused()
    # Should block ~1s
    start = asyncio.get_event_loop().time()
    await t.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.9


@pytest.mark.asyncio
async def test_halve_key():
    t = AsyncThrottler(["k1"], rps_per_key=10)
    await t.halve_key("k1")
    # No public accessor for rate; assert it doesn't blow up.
    key = await t.acquire()
    assert key == "k1"


@pytest.mark.asyncio
async def test_raise_if_no_keys():
    with pytest.raises(ValueError):
        AsyncThrottler([], rps_per_key=10)