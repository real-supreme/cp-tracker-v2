# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EnqueuedResponse(BaseModel):
    status: str = "enqueued"
    entity_type: str
    tag: str
    retry_after_seconds: int = 30


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_type: str
    entity_tag: str
    event_type: str
    payload: dict
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    paused: bool


class LaneStats(BaseModel):
    tracked: int
    e_max: int
    workers: int
    batch_size: int
    rps: float


class StatsResponse(BaseModel):
    paused: bool
    keys_configured: int
    keys_throttled: list[str]
    lanes: dict[str, LaneStats]


class ReviveResponse(BaseModel):
    revived: bool
    entity_type: str
    tag: str