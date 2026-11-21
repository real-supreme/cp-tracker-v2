# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timedelta, timezone

from app.coc.priority import (
    apply_cache_floor,
    compute_interval_seconds,
    compute_priority_score,
    roll_window,
)
from app.config import AdaptiveConfig, LaneConfig, PriorityConfig


def _lane(min_s=120, max_s=900):
    return LaneConfig(
        budget_fraction=0.4,
        min_interval_seconds=min_s,
        max_interval_seconds=max_s,
        calls_per_entity=1,
    )


def _adaptive(t1=3, t2=6):
    return AdaptiveConfig(enabled=True, stable_threshold_1=t1, stable_threshold_2=t2)


def test_interval_adaptive_backoff():
    lane, adaptive = _lane(), _adaptive()
    assert compute_interval_seconds(lane, adaptive, 0) == 120
    assert compute_interval_seconds(lane, adaptive, 3) == 240
    assert compute_interval_seconds(lane, adaptive, 6) == 480
    # capped by max
    lane2 = _lane(min_s=120, max_s=300)
    assert compute_interval_seconds(lane2, adaptive, 6) == 300


def test_priority_score_grows_with_signals():
    cfg = PriorityConfig(
        weight_access=1.0, weight_diff=2.0,
        diff_rate_cap=0.5, access_decay_hours=24, diff_window_hours=1,
    )
    cold = compute_priority_score(0, 0, 10, cfg)
    hot = compute_priority_score(100, 5, 10, cfg)
    assert hot > cold


def test_cache_floor_wins_when_larger():
    assert apply_cache_floor(120, 300, 900) == 300
    assert apply_cache_floor(120, 0, 900) == 120
    # capped by lane max
    assert apply_cache_floor(120, 10000, 900) == 900


def test_roll_window_resets():
    cfg = PriorityConfig(
        weight_access=1.0, weight_diff=2.0,
        diff_rate_cap=0.5, access_decay_hours=24, diff_window_hours=1,
    )
    old_start = datetime.now(timezone.utc) - timedelta(hours=2)
    d, p, a, new_start = roll_window(old_start, 3, 5, 10, cfg)
    assert (d, p, a) == (0, 0, 0)
    assert new_start > old_start