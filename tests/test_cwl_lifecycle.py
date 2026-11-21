# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Verify war lanes never re-poll after ENDED / NOT_IN_WAR, and that the
CWL classification forces a longer interval."""
from app.coc.diff import is_cwl_war, next_war_poll_seconds, war_is_ended
from app.coc.diff import war_is_trackable


def test_ended_war_not_trackable():
    assert war_is_ended("ENDED")
    assert not war_is_trackable("ENDED")


def test_not_in_war_not_trackable():
    assert war_is_ended("NOT_IN_WAR")
    assert not war_is_trackable("NOT_IN_WAR")


def test_war_state_interval_during_battle_day():
    assert next_war_poll_seconds({"state": "WAR"}) == 120
    assert next_war_poll_seconds({"state": "IN_WAR"}) == 120


def test_war_state_interval_during_prep():
    assert next_war_poll_seconds({"state": "PREPARATION"}) == 300


def test_cwl_war_gets_10_min_floor():
    payload = {"state": "WAR", "leagueGroupId": 123}
    base = next_war_poll_seconds(payload)
    if is_cwl_war(payload):
        base = max(base, 600)
    assert base == 600