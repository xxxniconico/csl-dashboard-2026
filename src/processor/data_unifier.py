import json
import re
from pathlib import Path
from typing import Any, Dict, List


def load_json_array(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def club_id(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", name).strip("_")
    return f"club_{safe}" if safe else "club_unknown"


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": event.get("type"),
        "player": event.get("player") or event.get("player_name") or "",
        "minute": event.get("minute"),
    }


def normalize_match(match: Dict[str, Any]) -> Dict[str, Any]:
    venue = match.get("venue") if isinstance(match.get("venue"), dict) else {}
    return {
        "match_id": str(match.get("match_id", "")),
        "date": match.get("date"),
        "venue": {
            "name": venue.get("name"),
            "city": venue.get("city"),
        },
        "home_club": match.get("home_club"),
        "away_club": match.get("away_club"),
        "status": match.get("status"),
        "score": {
            "home": match.get("score", {}).get("home"),
            "away": match.get("score", {}).get("away"),
        },
        "events": [normalize_event(e) for e in (match.get("events") or []) if isinstance(e, dict)],
    }


def merge_by_match_id(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        mid = str(row.get("match_id", "")).strip()
        if not mid:
            continue
        # last write wins (allows later sources to refresh stale records)
        merged[mid] = normalize_match(row)
    return sorted(merged.values(), key=lambda x: x["match_id"])


def build_standings(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table: Dict[str, Dict[str, Any]] = {}

    def ensure_team(name: str) -> Dict[str, Any]:
        if name not in table:
            table[name] = {
                "club_id": club_id(name),
                "club_name": name,
                "points": 0,
                "penalty_points": 0,
                "effective_points": 0,
                "played": 0,
                "w_d_l": [0, 0, 0],
                "summary": {"goals_for": 0, "goals_against": 0},
            }
        return table[name]

    for m in matches:
        if str(m.get("status", "")).lower() != "finished":
            continue
        home = m.get("home_club")
        away = m.get("away_club")
        if not home or not away:
            continue
        hs = m.get("score", {}).get("home")
        as_ = m.get("score", {}).get("away")
        if hs is None or as_ is None:
            continue
        hs_i, as_i = to_int(hs), to_int(as_)

        h = ensure_team(home)
        a = ensure_team(away)

        h["played"] += 1
        a["played"] += 1
        h["summary"]["goals_for"] += hs_i
        h["summary"]["goals_against"] += as_i
        a["summary"]["goals_for"] += as_i
        a["summary"]["goals_against"] += hs_i

        if hs_i > as_i:
            h["points"] += 3
            h["w_d_l"][0] += 1
            a["w_d_l"][2] += 1
        elif hs_i < as_i:
            a["points"] += 3
            a["w_d_l"][0] += 1
            h["w_d_l"][2] += 1
        else:
            h["points"] += 1
            a["points"] += 1
            h["w_d_l"][1] += 1
            a["w_d_l"][1] += 1

    rows = list(table.values())
    for r in rows:
        r["effective_points"] = r["points"] - r["penalty_points"]

    rows.sort(
        key=lambda x: (
            -x["effective_points"],
            -(x["summary"]["goals_for"] - x["summary"]["goals_against"]),
            -x["summary"]["goals_for"],
            x["club_name"],
        )
    )
    return rows


def build_league_block(league_id: str, name: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged_matches = merge_by_match_id(rows)
    return {
        "league_id": league_id,
        "name": name,
        "standings": build_standings(merged_matches),
        "matches": merged_matches,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    csl_path = root / "data" / "csl_matches_enriched.json"
    cfl_path = root / "data" / "csl_matches_enriched_cfl.json"
    output_path = root / "data" / "all_seasons_unified_index.json"

    csl_rows = load_json_array(csl_path)
    cfl_rows = load_json_array(cfl_path)

    leagues = [
        build_league_block("csl", "中超联赛", csl_rows),
        build_league_block("cfl", "中职联赛", cfl_rows),
    ]

    payload = {
        "season": "2026",
        "leagues": leagues,
    }
    save_json(output_path, payload)

    total_matches = sum(len(l["matches"]) for l in leagues)
    print(f"merged_total_matches: {total_matches}")
    print(f"league_count: {len(leagues)}")


if __name__ == "__main__":
    main()
