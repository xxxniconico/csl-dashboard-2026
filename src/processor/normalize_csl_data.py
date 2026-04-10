import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return 0
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def normalize_standings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "rank": to_int(row.get("rank")),
                "team_name": row.get("team_name", "").strip(),
                "played": to_int(row.get("played")),
                "won": to_int(row.get("won")),
                "drawn": to_int(row.get("drawn")),
                "lost": to_int(row.get("lost")),
                "goals_for": to_int(row.get("goals_for")),
                "goals_against": to_int(row.get("goals_against")),
                "goal_difference": to_int(row.get("goal_difference")),
                "points": to_int(row.get("points")),
                "recent_form": row.get("recent_form") or [],
            }
        )
    return sorted(normalized, key=lambda x: x["rank"] if x["rank"] > 0 else 9999)


def normalize_matches(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for row in rows:
        score = (row.get("score") or "").strip()
        normalized.append(
            {
                "round_name": row.get("round_name", "").strip(),
                "home_team": row.get("home_team", "").strip(),
                "away_team": row.get("away_team", "").strip(),
                "score": score,
                "status": row.get("status", "").strip() or ("finished" if score and score != ":" else "scheduled"),
            }
        )
    return normalized


def normalize_players(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "player_name": row.get("player_name", "").strip(),
                "team_name": row.get("team_name", "").strip(),
                "goals": to_int(row.get("goals")),
                "assists": to_int(row.get("assists")),
                "yellow_cards": row.get("yellow_cards"),
                "red_cards": row.get("red_cards"),
            }
        )

    # Higher goals first, then assists.
    return sorted(normalized, key=lambda x: (-x["goals"], -x["assists"], x["player_name"]))


def merge_cards_into_players(
    players: List[Dict[str, Any]],
    card_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for player in players:
        key = f"{player['player_name']}::{player['team_name']}"
        by_key[key] = player

    for row in card_rows:
        player_name = (row.get("player_name") or "").strip()
        team_name = (row.get("team_name") or "").strip()
        if not player_name:
            continue
        key = f"{player_name}::{team_name}"
        yellow = row.get("yellow_cards")
        red = row.get("red_cards")

        if key in by_key:
            if yellow is not None:
                by_key[key]["yellow_cards"] = to_int(yellow)
            if red is not None:
                by_key[key]["red_cards"] = to_int(red)
            continue

        # If team name is missing in card source, fallback to name-only match.
        if not team_name:
            matched_key = None
            for candidate_key in by_key:
                if candidate_key.startswith(f"{player_name}::"):
                    matched_key = candidate_key
                    break
            if matched_key:
                if yellow is not None:
                    by_key[matched_key]["yellow_cards"] = to_int(yellow)
                if red is not None:
                    by_key[matched_key]["red_cards"] = to_int(red)
                continue

        # Include card-only players if they were not in goal/assist rankings.
        by_key[key] = {
            "player_name": player_name,
            "team_name": team_name,
            "goals": 0,
            "assists": 0,
            "yellow_cards": to_int(yellow) if yellow is not None else None,
            "red_cards": to_int(red) if red is not None else None,
        }

    merged = list(by_key.values())
    return sorted(merged, key=lambda x: (-x["goals"], -x["assists"], x["player_name"]))


def merge_player_supplement(
    base_players: List[Dict[str, Any]],
    supplement_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for player in base_players:
        key = f"{player['player_name']}::{player['team_name']}"
        by_key[key] = player

    for row in supplement_rows:
        player_name = (row.get("player_name") or "").strip()
        team_name = (row.get("team_name") or "").strip()
        if not player_name:
            continue
        key = f"{player_name}::{team_name}"
        if key not in by_key:
            by_key[key] = {
                "player_name": player_name,
                "team_name": team_name,
                "goals": to_int(row.get("goals")),
                "assists": to_int(row.get("assists")),
                "yellow_cards": to_int(row.get("yellow_cards")) if row.get("yellow_cards") is not None else None,
                "red_cards": to_int(row.get("red_cards")) if row.get("red_cards") is not None else None,
            }
            continue

        current = by_key[key]
        for metric in ("goals", "assists", "yellow_cards", "red_cards"):
            if current.get(metric) is None and row.get(metric) is not None:
                current[metric] = to_int(row.get(metric))

    merged = list(by_key.values())
    return sorted(merged, key=lambda x: (-x["goals"], -x["assists"], x["player_name"]))


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data"

    standings_raw = load_json(data_dir / "csl_standings_raw.json")
    matches_raw = load_json(data_dir / "csl_match_results_raw.json")
    players_raw = load_json(data_dir / "csl_player_stats_raw.json")
    players_supplement_raw = load_json(data_dir / "csl_player_stats_leisu_raw.json")
    players_events_raw = load_json(data_dir / "csl_player_stats_events_raw.json")
    cards_raw = load_json(data_dir / "csl_cards_raw.json")

    standings = normalize_standings(standings_raw.get("standings", []))
    matches = normalize_matches(matches_raw.get("match_results", []))
    players = normalize_players(players_raw.get("player_stats", []))
    players = merge_player_supplement(players, players_supplement_raw.get("player_stats", []))
    players = merge_player_supplement(players, players_events_raw.get("player_stats", []))
    players = merge_cards_into_players(players, cards_raw.get("player_cards", []))

    yellow_red_missing = all(
        (p.get("yellow_cards") is None and p.get("red_cards") is None) for p in players
    )

    payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_files": [
                "csl_standings_raw.json",
                "csl_match_results_raw.json",
                "csl_player_stats_raw.json",
                "csl_player_stats_leisu_raw.json",
                "csl_player_stats_events_raw.json",
                "csl_cards_raw.json",
            ],
            "counts": {
                "standings": len(standings),
                "match_results": len(matches),
                "player_stats": len(players),
            },
            "quality_flags": {
                "yellow_red_cards_missing": yellow_red_missing,
            },
            "notes": [
                "player card stats are merged from Dongqiudi card rank candidates and Zhongzhilian disciplinary backfill.",
                "leisu player stats are merged as supplemental source for missing names/metrics.",
                "event-derived player stats are merged to guarantee player name coverage.",
                "if card values remain missing, add a richer source adapter for detailed disciplinary bulletins.",
            ],
        },
        "standings": standings,
        "match_results": matches,
        "player_stats": players,
    }

    output = data_dir / "csl_normalized.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"normalized output written: {output}")


if __name__ == "__main__":
    main()
