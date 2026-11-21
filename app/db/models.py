# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TrackedEntity(Base):
    __tablename__ = "tracked_entities"

    entity_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    tag: Mapped[str] = mapped_column(String(32), primary_key=True)

    # Scheduling
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    next_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Tracking window
    tracking_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tracking_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Snapshot
    payload: Mapped[dict | None] = mapped_column(JSONB)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Diff signals
    access_count_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    diff_count_window: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    poll_count_window: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stable_polls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Dead flag
    is_dead: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dead_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Worker coordination
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Diagnostics
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_tracked_due", "entity_type", "is_dead", "next_poll_at"),
        Index("ix_tracked_priority", "entity_type", "is_dead", "priority_score", "next_poll_at"),
        Index("ix_tracked_claims", "claimed_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_tag", "created_at"),
        Index("ix_audit_created", "created_at"),
    )


class ServiceEvent(Base):
    __tablename__ = "service_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key_name: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_service_events_type", "event_type", "created_at"),
    )
