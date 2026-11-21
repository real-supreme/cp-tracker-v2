# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
from app.coc.capacity import compute_capacity
from app.config import load_config


def test_capacity_matches_formula():
    cfg = load_config()
    cap = compute_capacity(cfg)

    aggregate = len(cfg.api_keys) * cfg.requests_per_second_per_key
    assert aggregate == 60  # 2 keys × 30/sec

    # clan lane: 60 rps × 0.40 fraction = 24 rps.
    # E_max = 24 × 300s / 1 = 7200
    assert cap.lanes["clan"].lane_rps == 24.0
    assert cap.lanes["clan"].e_max == 7200

    # player: 60 × 0.45 = 27 rps → E_max = 27 × 600 / 1 = 16,200
    assert cap.lanes["player"].lane_rps == 27.0
    assert cap.lanes["player"].e_max == 16200

    # war: 60 × 0.15 = 9 rps → E_max = 9 × 900 / 1 = 8,100
    assert cap.lanes["war"].lane_rps == 9.0
    assert cap.lanes["war"].e_max == 8100

    # semaphore = max(20, ceil(60 × 0.8)) = 48
    assert cap.semaphore == 48


def test_workers_scale_with_keys(monkeypatch):
    monkeypatch.setenv("COC_API_KEYS", ",".join(f"k{i}" for i in range(10)))
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)
    cfg = cfg_mod.load_config()
    cap = compute_capacity(cfg)
    # 10 keys: aggregate 300 rps, clan lane 120 rps, per_worker 40 → 3 workers
    assert cap.lanes["clan"].workers == 3
    assert cap.lanes["clan"].e_max == int(120 * 300)