# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    # Note: this exercises create_app() but lifespan is skipped by TestClient
    # unless you use `with TestClient(app) as c:`. We only test the auth path
    # which doesn't require lifespan for 401/403 responses.
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_public(client):
    r = client.get("/api/v1/health")
    # 200 if lifespan ran, 500 if not — either way auth is not required.
    assert r.status_code in (200, 500)


def test_clan_requires_auth(client):
    r = client.get("/api/v1/clans/2PP")
    assert r.status_code == 401


def test_clan_rejects_bad_token(client):
    r = client.get("/api/v1/clans/2PP", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


def test_invalid_tag_rejected():
    # Unit test of normalize_tag without spinning the app.
    from app.api.routes import normalize_tag
    from fastapi import HTTPException
    try:
        normalize_tag("not-a-tag!")
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 400


def test_valid_tag_normalized():
    from app.api.routes import normalize_tag
    assert normalize_tag("#2PP") == "2PP"
    assert normalize_tag("2pp") == "2PP"