import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_PROC = Path(__file__).resolve().parent
if str(_PROC) not in sys.path:
    sys.path.insert(0, str(_PROC))
from event_names import resolve_event_player_name
from player_team_utils import (
    collect_ranking_player_rows,
    exact_team_lookup_from_rows,
    fuzzy_team_substring_match,
)


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def normalize_name(value: Any) -> str:
    return str(value or "").strip()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data"
    candidates = [
        data_dir / "all_seasons_unified_index.json",
        data_dir / "csl_final_production_ready.json",
    ]

    src_file = None
    data = {}
    for p in candidates:
        if p.exists():
            data = load_json(p)
            src_file = p.name
            break

    if not src_file:
        raise FileNotFoundError("No source dataset found for player event aggregation.")

    ranking_rows = collect_ranking_player_rows(data_dir)
    rank_exact = exact_team_lookup_from_rows(ranking_rows)

    # Treat CSL/CFL as one competition for player stats.
    player_map: Dict[str, Dict[str, Any]] = {}
    team_votes: Dict[str, Counter] = defaultdict(Counter)
    for league in safe_list(data.get("leagues")):
        for match in safe_list(league.get("matches")):
            for event in safe_list(match.get("events")):
                name = resolve_event_player_name(event) or normalize_name(
                    event.get("player") or event.get("player_name")
                )
                if not name:
                    continue
                team = normalize_name(event.get("team_name") or event.get("club_name"))
                if team:
                    team_votes[name][team] += 1
                key = name
                if key not in player_map:
                    player_map[key] = {
                        "player_name": name,
                        "team_name": "",
                        "goals": 0,
                        "assists": 0,
                        "yellow_cards": 0,
                        "red_cards": 0,
                        "source_note": "aggregated_from_match_events",
                    }
                row = player_map[key]
                etype = normalize_name(event.get("type")).lower()
                if etype == "goal":
                    row["goals"] += 1
                elif etype == "yellow_card":
                    row["yellow_cards"] += 1
                elif etype == "red_card":
                    row["red_cards"] += 1

    for name, row in player_map.items():
        if team_votes.get(name):
            row["team_name"] = team_votes[name].most_common(1)[0][0]
            continue
        if name in rank_exact:
            row["team_name"] = rank_exact[name]
            row["source_note"] = "aggregated_from_match_events+ranking_team_exact"
            continue
        fz = fuzzy_team_substring_match(name, ranking_rows)
        if fz:
            row["team_name"] = fz
            row["source_note"] = "aggregated_from_match_events+ranking_team_fuzzy"

    rows = sorted(
        player_map.values(),
        key=lambda x: (-int(x.get("goals", 0)), -int(x.get("assists", 0)), x.get("player_name", "")),
    )

    payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "dataset": "player_stats_from_events",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_file": src_file,
            "count": len(rows),
            "notes": [
                "Built from match event streams to guarantee player names.",
                "team_name is the majority vote from event.team_name / club_name when present (e.g. CFL API goals/cards by side).",
                "Assist metric is set to 0 when unavailable in event feed.",
            ],
        },
        "player_stats": rows,
    }
    out_path = data_dir / "csl_player_stats_events_raw.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"player stats (events) written: {out_path}")
    print(f"rows: {len(rows)}")


if __name__ == "__main__":
    main()
