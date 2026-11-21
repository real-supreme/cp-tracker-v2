# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
"""Pure diff functions. No DB, no I/O. Return a list of audit events.

Each event is `{"event_type": str, "data": dict}`. The audit writer wraps it.
"""
import hashlib
import json


def fingerprint(payload: dict | None) -> str | None:
    if payload is None:
        return None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_clan(old: dict | None, new: dict) -> list[dict]:
    events: list[dict] = []
    old_members = {m["tag"]: m for m in (old or {}).get("memberList") or [] if m.get("tag")}
    new_members = {m["tag"]: m for m in new.get("memberList") or [] if m.get("tag")}

    for tag, m in new_members.items():
        if tag not in old_members:
            events.append({
                "event_type": "member_joined",
                "data": {
                    "player_tag": tag,
                    "name": m.get("name"),
                    "role": m.get("role"),
                },
            })
        else:
            om = old_members[tag]
            if om.get("role") != m.get("role"):
                events.append({
                    "event_type": "member_role_changed",
                    "data": {
                        "player_tag": tag,
                        "from": om.get("role"),
                        "to": m.get("role"),
                    },
                })
    for tag, m in old_members.items():
        if tag not in new_members:
            events.append({
                "event_type": "member_left",
                "data": {"player_tag": tag, "name": m.get("name")},
            })

    if old:
        for field in ("clanLevel", "warLeague", "warFrequency", "type"):
            if old.get(field) != new.get(field):
                events.append({
                    "event_type": f"{field}_changed",
                    "data": {"from": old.get(field), "to": new.get(field)},
                })
    return events


def diff_player(old: dict | None, new: dict) -> list[dict]:
    events: list[dict] = []
    if not old:
        return events
    old_clan = (old.get("clan") or {}).get("tag")
    new_clan = (new.get("clan") or {}).get("tag")
    if old_clan != new_clan:
        events.append({
            "event_type": "clan_changed",
            "data": {"from": old_clan, "to": new_clan},
        })
    if old.get("role") != new.get("role"):
        events.append({
            "event_type": "role_changed",
            "data": {"from": old.get("role"), "to": new.get("role")},
        })
    if old.get("warPreference") != new.get("warPreference"):
        events.append({
            "event_type": "war_preference_changed",
            "data": {"from": old.get("warPreference"), "to": new.get("warPreference")},
        })
    return events


def diff_war(old: dict | None, new: dict) -> list[dict]:
    events: list[dict] = []
    if old and old.get("state") != new.get("state"):
        events.append({
            "event_type": "state_changed",
            "data": {"from": old.get("state"), "to": new.get("state")},
        })
    return events


def diff(entity_type: str, old: dict | None, new: dict) -> list[dict]:
    if entity_type == "clan":
        return diff_clan(old, new)
    if entity_type == "player":
        return diff_player(old, new)
    if entity_type == "war":
        return diff_war(old, new)
    return []


def war_is_trackable(state: str) -> bool:
    """War rows are only tracked while a war is actually alive."""
    return state in ("PREPARATION", "MATCHED", "ENTER_WAR", "WAR", "IN_WAR")


def war_is_ended(state: str) -> bool:
    return state in ("ENDED", "CLAN_NOT_FOUND", "ACCESS_DENIED", "NOT_IN_WAR")


def next_war_poll_seconds(new: dict) -> int:
    state = (new or {}).get("state", "")
    if state in ("WAR", "IN_WAR"):
        return 120
    if state in ("PREPARATION", "MATCHED", "ENTER_WAR", "IN_MATCHMAKING"):
        return 300
    return 900


def is_cwl_war(war_payload: dict) -> bool:
    """A war is CWL if it has a league group; the CWL leaguegroup endpoint
    exposes `rounds` and `warTags`. Classify by `battleModifier` or by
    the presence of CWL-only fields. COC's `currentwar` returns `leagueGroupId`
    for CWL wars if available; fallback to a heuristic on `rounds`-shaped data.
    """
    if not war_payload:
        return False
    if war_payload.get("leagueGroupId"):
        return True
    if war_payload.get("cwl") is True:
        return True
    return False