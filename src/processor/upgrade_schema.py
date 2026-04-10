import argparse
import json
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


VENUES = [
    ("上海体育场", "上海", 56000),
    ("北京工人体育场", "北京", 68000),
    ("天津奥体中心", "天津", 54000),
    ("成都凤凰山体育公园", "成都", 60000),
    ("济南奥体中心", "济南", 56000),
    ("深圳大运中心", "深圳", 60000),
    ("武汉体育中心", "武汉", 54000),
    ("青岛青春足球场", "青岛", 50000),
]

POSITIONS = ["FW", "MF", "DF", "GK"]


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_team_players(old_data: Dict[str, Any], rng: random.Random) -> Dict[str, List[Dict[str, Any]]]:
    by_team: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in old_data.get("player_stats", []):
        team = (p.get("team_name") or "").strip()
        name = (p.get("player_name") or "").strip()
        if not team or not name:
            continue
        by_team[team].append(
            {
                "player_name": name,
                "team_name": team,
                "position": rng.choice(POSITIONS),
                "shirt_number": rng.randint(1, 35),
                "goals": int(p.get("goals", 0) or 0),
                "assists": int(p.get("assists", 0) or 0),
                "yellow_cards": p.get("yellow_cards"),
                "red_cards": p.get("red_cards"),
            }
        )
    return by_team


def ensure_team_roster(
    team_name: str,
    team_players: Dict[str, List[Dict[str, Any]]],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    roster = list(team_players.get(team_name, []))
    while len(roster) < 11:
        idx = len(roster) + 1
        roster.append(
            {
                "player_name": f"{team_name}球员{idx}",
                "team_name": team_name,
                "position": rng.choice(POSITIONS),
                "shirt_number": rng.randint(1, 35),
                "goals": 0,
                "assists": 0,
                "yellow_cards": None,
                "red_cards": None,
            }
        )
    return roster


def build_match_plan(teams: List[str], rng: random.Random) -> List[Tuple[str, str]]:
    desired = {t: rng.randint(2, 3) for t in teams}
    pairs: List[Tuple[str, str]] = []
    pair_set = set()
    max_iters = 5000

    while max(desired.values(), default=0) > 0 and max_iters > 0:
        max_iters -= 1
        candidates = [t for t, c in desired.items() if c > 0]
        if len(candidates) < 2:
            break
        home = rng.choice(candidates)
        away_candidates = [t for t in candidates if t != home and tuple(sorted((home, t))) not in pair_set]
        if not away_candidates:
            # fallback: allow repeated opponent to complete quotas
            away_candidates = [t for t in candidates if t != home]
        if not away_candidates:
            break
        away = rng.choice(away_candidates)

        desired[home] -= 1
        desired[away] -= 1
        pairs.append((home, away))
        pair_set.add(tuple(sorted((home, away))))

    return pairs


def build_goal_events(
    home_team: str,
    away_team: str,
    home_roster: List[Dict[str, Any]],
    away_roster: List[Dict[str, Any]],
    home_goals: int,
    away_goals: int,
    rng: random.Random,
    start_id: int,
) -> Tuple[List[Dict[str, Any]], int]:
    events = []
    event_id = start_id

    def pick_scorer(roster: List[Dict[str, Any]]) -> Dict[str, Any]:
        # weighted by existing goals, but still random.
        weighted = []
        for p in roster:
            w = max(1, int(p.get("goals", 0) or 0) + 1)
            weighted.extend([p] * w)
        return rng.choice(weighted)

    used_minutes = set()
    for _ in range(home_goals):
        minute = rng.randint(4, 90)
        while minute in used_minutes:
            minute = rng.randint(4, 90)
        used_minutes.add(minute)
        scorer = pick_scorer(home_roster)
        assister = rng.choice(home_roster) if rng.random() < 0.55 else None
        event = {
            "event_id": f"evt-{event_id}",
            "minute": minute,
            "type": "goal",
            "team_name": home_team,
            "player": {
                "name": scorer["player_name"],
                "team_name": home_team,
                "position": scorer["position"],
                "shirt_number": scorer["shirt_number"],
            },
        }
        if assister:
            event["assist_player"] = assister["player_name"]
        events.append(event)
        event_id += 1

    for _ in range(away_goals):
        minute = rng.randint(4, 90)
        while minute in used_minutes:
            minute = rng.randint(4, 90)
        used_minutes.add(minute)
        scorer = pick_scorer(away_roster)
        assister = rng.choice(away_roster) if rng.random() < 0.55 else None
        event = {
            "event_id": f"evt-{event_id}",
            "minute": minute,
            "type": "goal",
            "team_name": away_team,
            "player": {
                "name": scorer["player_name"],
                "team_name": away_team,
                "position": scorer["position"],
                "shirt_number": scorer["shirt_number"],
            },
        }
        if assister:
            event["assist_player"] = assister["player_name"]
        events.append(event)
        event_id += 1

    return events, event_id


def build_card_events(
    home_team: str,
    away_team: str,
    home_roster: List[Dict[str, Any]],
    away_roster: List[Dict[str, Any]],
    rng: random.Random,
    start_id: int,
) -> Tuple[List[Dict[str, Any]], int]:
    events = []
    event_id = start_id
    yellow_total = rng.randint(1, 5)
    red_total = 1 if rng.random() < 0.22 else 0

    for _ in range(yellow_total):
        team_name, roster = (home_team, home_roster) if rng.random() < 0.5 else (away_team, away_roster)
        player = rng.choice(roster)
        events.append(
            {
                "event_id": f"evt-{event_id}",
                "minute": rng.randint(10, 90),
                "type": "yellow_card",
                "team_name": team_name,
                "player": {
                    "name": player["player_name"],
                    "team_name": team_name,
                    "position": player["position"],
                    "shirt_number": player["shirt_number"],
                },
            }
        )
        event_id += 1

    for _ in range(red_total):
        team_name, roster = (home_team, home_roster) if rng.random() < 0.5 else (away_team, away_roster)
        player = rng.choice(roster)
        events.append(
            {
                "event_id": f"evt-{event_id}",
                "minute": rng.randint(35, 90),
                "type": "red_card",
                "team_name": team_name,
                "player": {
                    "name": player["player_name"],
                    "team_name": team_name,
                    "position": player["position"],
                    "shirt_number": player["shirt_number"],
                },
            }
        )
        event_id += 1

    return events, event_id


def upgrade_schema(old_data: Dict[str, Any], seed: int = 20260410) -> Dict[str, Any]:
    rng = random.Random(seed)
    standings = old_data.get("standings", [])
    if not standings:
        raise ValueError("input data has no standings array")

    teams = [s.get("team_name", "").strip() for s in standings if s.get("team_name")]
    if len(teams) < 2:
        raise ValueError("not enough teams to synthesize matches")

    team_players = build_team_players(old_data, rng)
    match_plan = build_match_plan(teams, rng)

    start_time = datetime(2026, 3, 1, 11, 30, tzinfo=timezone.utc)
    matches = []
    event_seq = 1

    for idx, (home, away) in enumerate(match_plan, start=1):
        home_roster = ensure_team_roster(home, team_players, rng)
        away_roster = ensure_team_roster(away, team_players, rng)
        home_goals = rng.randint(0, 4)
        away_goals = rng.randint(0, 3)
        goal_events, event_seq = build_goal_events(
            home, away, home_roster, away_roster, home_goals, away_goals, rng, event_seq
        )
        card_events, event_seq = build_card_events(home, away, home_roster, away_roster, rng, event_seq)
        events = sorted(goal_events + card_events, key=lambda e: (e["minute"], e["event_id"]))
        venue_name, city, capacity = VENUES[(idx - 1) % len(VENUES)]

        matches.append(
            {
                "match_id": f"CSL-2026-M{idx:03d}",
                "status": "completed",
                "round": f"Round {(idx - 1) // 4 + 1}",
                "kickoff_time_utc": (start_time + timedelta(days=idx)).isoformat(),
                "venue": {
                    "name": venue_name,
                    "city": city,
                    "capacity": capacity,
                },
                "home_team": home,
                "away_team": away,
                "score": {"home": home_goals, "away": away_goals},
                "events": events,
            }
        )

    team_entries = []
    penalty_team_name = standings[min(2, len(standings) - 1)]["team_name"]
    for s in standings:
        team_name = s.get("team_name", "")
        team_entries.append(
            {
                "team_name": team_name,
                "rank": s.get("rank"),
                "played": s.get("played"),
                "won": s.get("won"),
                "drawn": s.get("drawn"),
                "lost": s.get("lost"),
                "goals_for": s.get("goals_for"),
                "goals_against": s.get("goals_against"),
                "goal_difference": s.get("goal_difference"),
                "points": s.get("points"),
                "penalty_points": 3 if team_name == penalty_team_name else 0,
                "players": ensure_team_roster(team_name, team_players, rng),
            }
        )

    return {
        "meta": {
            "schema_version": "2.1",
            "migration": "flat_to_hierarchical_event_driven",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_schema_version": old_data.get("meta", {}).get("schema_version", "1.x-flat"),
            "seed": seed,
            "notes": [
                "Synthetic matches/events/venues were generated for migration testing.",
                "Penalty points injected for one team to validate Points Engine behavior.",
            ],
        },
        "leagues": [
            {
                "league_id": "CSL-2026",
                "name": "Chinese Super League",
                "country": "China",
                "season": 2026,
                "teams": team_entries,
                "matches": matches,
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Upgrade CSL normalized schema to hierarchical v2.1")
    parser.add_argument(
        "--input",
        default=str(Path(__file__).resolve().parents[2] / "data" / "csl_normalized.json"),
        help="Input flat JSON path",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[2] / "data" / "csl_normalized.json"),
        help="Output upgraded JSON path",
    )
    parser.add_argument("--seed", type=int, default=20260410, help="Random seed for deterministic synthesis")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    old_data = load_json(input_path)
    upgraded = upgrade_schema(old_data, seed=args.seed)
    save_json(output_path, upgraded)
    print(f"schema upgraded: {output_path}")
    print(f"leagues: {len(upgraded.get('leagues', []))}")
    if upgraded.get("leagues"):
        league = upgraded["leagues"][0]
        print(f"teams: {len(league.get('teams', []))}, matches: {len(league.get('matches', []))}")


if __name__ == "__main__":
    main()
