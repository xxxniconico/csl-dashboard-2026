"""Resolve player display name from heterogeneous event dicts (crawler/API/HTML)."""
from __future__ import annotations

from typing import Any, Dict


def resolve_event_player_name(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    keys = (
        "player",
        "player_name",
        "name",
        "eventPlayerName",
        "event_player_name",
        "athleteName",
        "playerName",
        "athlete_name",
        "label",
        "personName",
        "shooter",
        "scorer",
        "持卡人",
    )
    for key in keys:
        v = event.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""
