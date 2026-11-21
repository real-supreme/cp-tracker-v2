# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Single-point configuration loader.

Secrets come from the environment (`.env`, loaded by docker compose or your
shell). Tunables come from `config/config.yaml`. Both are parsed once and
exposed as an immutable dataclass tree.
"""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class LaneConfig:
    budget_fraction: float
    min_interval_seconds: int
    max_interval_seconds: int
    calls_per_entity: int


@dataclass(frozen=True)
class AdaptiveConfig:
    enabled: bool
    stable_threshold_1: int
    stable_threshold_2: int


@dataclass(frozen=True)
class PriorityConfig:
    weight_access: float
    weight_diff: float
    diff_rate_cap: float
    access_decay_hours: int
    diff_window_hours: int


@dataclass(frozen=True)
class Config:
    # COC
    base_url: str
    request_timeout_seconds: int
    max_retries: int
    throttle_retry_seconds: int
    service_unavailable_pause_seconds: int
    assumed_p95_latency_seconds: float
    per_worker_rate_per_second: int
    coc_key_name: str

    # Throttling
    requests_per_second_per_key: int
    effective_rate_pct_of_max: float
    max_concurrent_requests: int
    rpm_halve_enabled: bool
    rpm_halve_threshold_pct: float
    rpm_halve_window_seconds: int
    rpm_halve_recovery_seconds: int

    # Polling
    auto_workers: bool
    clan_workers: int | None
    player_workers: int | None
    war_workers: int | None
    write_batch_size: int
    stale_claim_seconds: int
    clan: LaneConfig
    player: LaneConfig
    war: LaneConfig
    adaptive: AdaptiveConfig

    # Priority
    priority: PriorityConfig

    # CWL
    cwl_cooldown_days: int

    # Tracking
    fastapi_priority: int
    scheduled_priority: int
    default_ttl_days: int
    cleanup_interval_seconds: int
    dead_entity_purge_days: int

    # API
    client_rpm_limit: int
    stats_require_auth: bool

    # Alerts
    email_cooldown_seconds: int

    # Server
    host: str
    port: int
    cors_origins: list[str]
    behind_tls: bool

    # Init
    init_clans: list[str]
    init_players: list[str]

    # Secrets from env
    api_keys: list[str]
    coc_email: str
    coc_password: str
    api_client_keys: dict[str, str]
    ssl_root_cert: str | None
    database_url: str
    smtp: "SmtpConfig | None"

    log_level: str = field(default="INFO")


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    alert_email: str


def _parse_client_keys(raw: str) -> dict[str, str]:
    """`name:secret,name2:secret2` -> {"secret": "name"} lookup, plus inverse."""
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise RuntimeError(f"API_CLIENT_KEYS entry missing ':' — got {item!r}")
        name, secret = item.split(":", 1)
        name, secret = name.strip(), secret.strip()
        if not name or not secret:
            raise RuntimeError("API_CLIENT_KEYS entry has empty name or secret")
        out[secret] = name
    return out


def _parse_coc_keys(raw: str) -> list[str]:
    keys = [k.strip() for k in (raw or "").split(",") if k.strip()]
    if not keys:
        raise RuntimeError(
            "COC_API_KEYS is empty. Provide at least one API key in .env."
        )
    return keys


def _parse_smtp() -> SmtpConfig | None:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return None
    return SmtpConfig(
        host=host,
        port=int(os.getenv("SMTP_PORT", "587")),
        user=os.getenv("SMTP_USER", "").strip(),
        password=os.getenv("SMTP_PASSWORD", "").strip(),
        alert_email=os.getenv("ALERT_EMAIL", "").strip(),
    )


@lru_cache(maxsize=1)
def load_config() -> Config:
    path = Path(os.getenv("CONFIG_PATH", "config/config.yaml"))
    if not path.exists():
        raise RuntimeError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    api_keys = _parse_coc_keys(os.getenv("COC_API_KEYS", ""))
    database_url = os.getenv("DATABASE_URL", "").strip()
    ssl_root_cert=os.getenv("DATABASE_SSL_ROOT_CERT") or None
    if not database_url:
        raise RuntimeError("DATABASE_URL is empty.")
    api_client_keys = _parse_client_keys(os.getenv("API_CLIENT_KEYS", ""))
    
    max_concurrent = raw["throttling"].get("max_concurrent_requests")

    return Config(
        base_url=raw["coc"]["base_url"],
        request_timeout_seconds=int(raw["coc"]["request_timeout_seconds"]),
        max_retries=int(raw["coc"]["max_retries"]),
        throttle_retry_seconds=int(raw["coc"]["throttle_retry_seconds"]),
        service_unavailable_pause_seconds=int(raw["coc"]["service_unavailable_pause_seconds"]),
        coc_key_name=str(raw["coc"].get("coc_key_name", "")),
        assumed_p95_latency_seconds=float(raw["coc"]["assumed_p95_latency_seconds"]),
        per_worker_rate_per_second=int(raw["coc"]["per_worker_rate_per_second"]),

        requests_per_second_per_key=int(raw["throttling"]["requests_per_second_per_key"]),
        effective_rate_pct_of_max=float(raw["throttling"]["effective_rate_pct_of_max"]),
        max_concurrent_requests=int(max_concurrent) if max_concurrent else 0,  # 0 = auto
        rpm_halve_enabled=bool(raw["throttling"]["rpm_halve_enabled"]),
        rpm_halve_threshold_pct=float(raw["throttling"]["rpm_halve_threshold_pct"]),
        rpm_halve_window_seconds=int(raw["throttling"]["rpm_halve_window_seconds"]),
        rpm_halve_recovery_seconds=int(raw["throttling"]["rpm_halve_recovery_seconds"]),

        auto_workers=bool(raw["polling"]["auto_workers"]),
        clan_workers=raw["polling"].get("clan_workers"),
        player_workers=raw["polling"].get("player_workers"),
        war_workers=raw["polling"].get("war_workers"),
        write_batch_size=int(raw["polling"]["write_batch_size"]),
        stale_claim_seconds=int(raw["polling"]["stale_claim_seconds"]),
        clan=LaneConfig(**raw["polling"]["clan"]),
        player=LaneConfig(**raw["polling"]["player"]),
        war=LaneConfig(**raw["polling"]["war"]),
        adaptive=AdaptiveConfig(**raw["polling"]["adaptive_intervals"]),

        priority=PriorityConfig(**raw["priority"]),

        cwl_cooldown_days=int(raw["cwl"]["cooldown_days"]),

        fastapi_priority=int(raw["tracking"]["fastapi_priority"]),
        scheduled_priority=int(raw["tracking"]["scheduled_priority"]),
        default_ttl_days=int(raw["tracking"]["default_ttl_days"]),
        cleanup_interval_seconds=int(raw["tracking"]["cleanup_interval_seconds"]),
        dead_entity_purge_days=int(raw["tracking"]["dead_entity_purge_days"]),

        client_rpm_limit=int(raw["api"]["client_rpm_limit"]),
        stats_require_auth=bool(raw["api"]["stats_require_auth"]),

        email_cooldown_seconds=int(raw["alerts"]["email_cooldown_seconds"]),

        host=raw["server"]["host"],
        port=int(raw["server"]["port"]),
        cors_origins=list(raw["server"]["cors_origins"] or []),
        behind_tls=bool(raw["server"]["behind_tls"]),

        init_clans=[c.lstrip("#") for c in (raw["init"]["clans"] or [])],
        init_players=[p.lstrip("#") for p in (raw["init"]["players"] or [])],

        api_keys=api_keys,
        coc_email=os.getenv("COC_EMAIL", "").strip(),
        coc_password=os.getenv("COC_PASSWORD", "").strip(),
        api_client_keys=api_client_keys,
        database_url=database_url,
        ssl_root_cert=ssl_root_cert,
        smtp=_parse_smtp(),
        log_level=os.getenv("LOG_LEVEL", raw["logging"]["level"]),
    )