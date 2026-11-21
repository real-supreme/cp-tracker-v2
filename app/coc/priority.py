# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Priority + adaptive interval math.

Inputs are per-row signals kept in the DB:
  - access_count_24h      (bumped by FastAPI hits)
  - diff_count_window     (bumped by polls that produced diffs)
  - poll_count_window     (bumped by every poll)
  - window_started_at     (when the current rolling window began)
  - stable_polls          (consecutive polls with no diff)

Outputs:
  - priority_score        (0..~10, used for ORDER BY when claiming)
  - interval_seconds      (how far to push next_poll_at)
"""
import math
from datetime import datetime, timedelta, timezone

from app.config import AdaptiveConfig, LaneConfig, PriorityConfig


def compute_priority_score(
    access_count_24h: int,
    diff_count_window: int,
    poll_count_window: int,
    cfg: PriorityConfig,
) -> float:
    access_signal = math.log1p(access_count_24h)
    if poll_count_window > 0:
        diff_rate = diff_count_window / poll_count_window
    else:
        diff_rate = 0.0
    volatility_signal = min(diff_rate, cfg.diff_rate_cap)
    return cfg.weight_access * access_signal + cfg.weight_diff * volatility_signal


def compute_interval_seconds(
    lane: LaneConfig,
    adaptive: AdaptiveConfig,
    stable_polls: int,
) -> int:
    """Apply adaptive backoff on top of the lane's min interval.

    ≤ threshold_1 stable polls: 1× min
    ≥ threshold_1 stable polls: 2× min
    ≥ threshold_2 stable polls: 4× min (capped at max)
    """
    base = lane.min_interval_seconds
    if not adaptive.enabled:
        return base
    if stable_polls >= adaptive.stable_threshold_2:
        candidate = base * 4
    elif stable_polls >= adaptive.stable_threshold_1:
        candidate = base * 2
    else:
        candidate = base
    return min(candidate, lane.max_interval_seconds)


def roll_window(
    window_started_at: datetime | None,
    diff_count_window: int,
    poll_count_window: int,
    access_count_24h: int,
    priority_cfg: PriorityConfig,
) -> tuple[int, int, int, datetime]:
    """Return (diff_count, poll_count, access_count, window_started_at) after
    applying rolling window resets. Called before each poll writes its result.
    """
    now = datetime.now(timezone.utc)
    if window_started_at is None:
        window_started_at = now - timedelta(hours=priority_cfg.diff_window_hours)
    window_age = now - window_started_at
    if window_age >= timedelta(hours=priority_cfg.diff_window_hours):
        diff_count_window = 0
        poll_count_window = 0
        window_started_at = now
    if window_age >= timedelta(hours=priority_cfg.access_decay_hours):
        access_count_24h = 0
    return diff_count_window, poll_count_window, access_count_24h, window_started_at


def apply_cache_floor(
    interval_seconds: int, cache_max_age: int, lane_max_seconds: int
) -> int:
    """Cache-Control wins if larger than our floor, but never exceed the lane
    cap (so a single 1-day cache header can't park a row for a day)."""
    candidate = max(interval_seconds, cache_max_age) if cache_max_age > 0 else interval_seconds
    return min(candidate, lane_max_seconds)