# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
from app.coc.diff import (
    diff_clan,
    diff_player,
    diff_war,
    fingerprint,
    is_cwl_war,
    war_is_ended,
    war_is_trackable,
)


def test_member_joined_and_left():
    old = {"memberList": [{"tag": "#A", "name": "a", "role": "member"}]}
    new = {"memberList": [{"tag": "#B", "name": "b", "role": "elder"}]}
    events = diff_clan(old, new)
    kinds = {e["event_type"] for e in events}
    assert "member_joined" in kinds
    assert "member_left" in kinds


def test_role_change():
    old = {"memberList": [{"tag": "#A", "role": "member"}]}
    new = {"memberList": [{"tag": "#A", "role": "elder"}]}
    events = diff_clan(old, new)
    assert events[0]["event_type"] == "member_role_changed"
    assert events[0]["data"]["to"] == "elder"


def test_first_observation_is_silent():
    events = diff_clan(None, {"memberList": [{"tag": "#A"}]})
    assert events == []


def test_player_clan_change():
    old = {"clan": {"tag": "#A"}, "role": "member"}
    new = {"clan": {"tag": "#B"}, "role": "member"}
    events = diff_player(old, new)
    assert events[0]["event_type"] == "clan_changed"


def test_war_state_change():
    events = diff_war({"state": "PREPARATION"}, {"state": "WAR"})
    assert events[0]["event_type"] == "state_changed"


def test_war_is_trackable_states():
    for s in ("PREPARATION", "MATCHED", "ENTER_WAR", "WAR", "IN_WAR"):
        assert war_is_trackable(s)
    for s in ("ENDED", "CLAN_NOT_FOUND", "ACCESS_DENIED", "NOT_IN_WAR"):
        assert war_is_ended(s)
        assert not war_is_trackable(s)


def test_fingerprint_stable_and_distinct():
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint({"a": 1})


def test_cwl_classification():
    assert is_cwl_war({"leagueGroupId": 123})
    assert is_cwl_war({"cwl": True})
    assert not is_cwl_war({"state": "WAR"})