"""从射手/助攻榜等 ranking JSON 为球员名补全俱乐部（含简称与全名子串匹配）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def normalize_name(value: Any) -> str:
    return str(value or "").strip()


def collect_ranking_player_rows(data_dir: Path) -> List[Dict[str, Any]]:
    rows_out: List[Dict[str, Any]] = []
    for fname in ("csl_player_stats_raw.json", "csl_player_stats_leisu_raw.json"):
        p = data_dir / fname
        if not p.is_file():
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = payload.get("player_stats") if isinstance(payload.get("player_stats"), list) else []
        if not rows and isinstance(payload.get("rows"), list):
            rows = payload["rows"]
        for row in rows:
            if isinstance(row, dict):
                rows_out.append(row)
    return rows_out


def exact_team_lookup_from_rows(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    from collections import Counter, defaultdict

    votes: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        name = normalize_name(row.get("player_name"))
        team = normalize_name(row.get("team_name"))
        if name and team:
            votes[name][team] += 1
    return {n: c.most_common(1)[0][0] for n, c in votes.items() if c}


def fuzzy_team_substring_match(player_name: str, ranking_rows: List[Dict[str, Any]]) -> str:
    """
    榜内「全名」包含事件里的短名时（如 韦林顿-席尔瓦 ⊃ 席尔瓦），取最长全名对应球队。
    精确同名应由 exact_team_lookup_from_rows 先处理。
    """
    short = normalize_name(player_name)
    if len(short) < 2:
        return ""
    best_team = ""
    best_full_len = -1
    for row in ranking_rows:
        full = normalize_name(row.get("player_name"))
        team = normalize_name(row.get("team_name"))
        if not full or not team:
            continue
        if short == full:
            continue
        if short in full and len(full) > best_full_len:
            best_team = team
            best_full_len = len(full)
    return best_team
