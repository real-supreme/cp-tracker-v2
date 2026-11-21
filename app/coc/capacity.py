# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Capacity math.

Given keys and per-lane config, compute:
  - E_max: how many entities of this lane we can refresh at the configured
           interval without exceeding our budget.
  - workers: how many parallel asyncio tasks to spawn per lane.
  - batch_size: rows to claim per worker per cycle.
  - semaphore: concurrency ceiling for HTTP requests.

The formulas:
  aggregate_rps   = keys * rpm_per_key / 60
  lane_rps        = aggregate_rps * budget_fraction
  E_max           = floor(lane_rps * interval / calls_per_entity)
  workers         = ceil(lane_rps / per_worker_rate)
  batch_size      = max(4, ceil(lane_rps / workers * cycle_target_seconds))
  semaphore       = ceil(aggregate_rps * assumed_p95_latency)
"""
from dataclasses import dataclass
import math

from app.config import Config, LaneConfig


CYCLE_TARGET_SECONDS = 5.0  # aim for a full worker cycle every ~5s


@dataclass(frozen=True)
class LaneCapacity:
    lane: str
    e_max: int
    workers: int
    batch_size: int
    lane_rps: float


@dataclass(frozen=True)
class Capacity:
    aggregate_rps: float
    semaphore: int
    lanes: dict[str, LaneCapacity]


def _compute_lane(
    name: str, cfg: LaneConfig, aggregate_rps: float, per_worker_rate: float
) -> LaneCapacity:
    lane_rps = aggregate_rps * cfg.budget_fraction
    e_max = int((lane_rps * cfg.min_interval_seconds) / max(cfg.calls_per_entity, 1))
    workers = max(1, math.ceil(lane_rps / max(per_worker_rate, 1)))
    per_worker_rps = lane_rps / workers
    batch_size = max(4, math.ceil(per_worker_rps * CYCLE_TARGET_SECONDS))
    return LaneCapacity(
        lane=name,
        e_max=e_max,
        workers=workers,
        batch_size=batch_size,
        lane_rps=lane_rps,
    )


def compute_capacity(cfg: Config) -> Capacity:
    effective_rps = cfg.requests_per_second_per_key * cfg.effective_rate_pct_of_max
    aggregate_rps = len(cfg.api_keys) * effective_rps
    semaphore = cfg.max_concurrent_requests or max(
        20, math.ceil(aggregate_rps * cfg.assumed_p95_latency_seconds)
    )

    lanes = {
        "clan": _compute_lane("clan", cfg.clan, aggregate_rps, cfg.per_worker_rate_per_second),
        "player": _compute_lane("player", cfg.player, aggregate_rps, cfg.per_worker_rate_per_second),
        "war": _compute_lane("war", cfg.war, aggregate_rps, cfg.per_worker_rate_per_second),
    }
    return Capacity(aggregate_rps=aggregate_rps, semaphore=semaphore, lanes=lanes)


def format_capacity_report(cap: Capacity, cfg: Config) -> str:
    lines = [
        f"COC capacity | keys={len(cfg.api_keys)} "
        f"aggregate={cap.aggregate_rps:.1f} rps "
        f"semaphore={cap.semaphore}"
    ]
    for name, lane in cap.lanes.items():
        lines.append(
            f"  {name:<7} E_max={lane.e_max:>7} "
            f"workers={lane.workers:>2} batch={lane.batch_size:>4} "
            f"rps={lane.lane_rps:.1f}"
        )
    return "\n".join(lines)