import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from source_adapters.dongqiudi_adapter import fetch_player_cards
from source_adapters.zhongzhilian_adapter import fetch_disciplinary_events


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.dongqiudi.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return s


def merge_card_rows(dongqiudi_data: dict, disciplinary_data: dict) -> list:
    merged = {}

    def key_of(player_name: str, team_name: str) -> str:
        return f"{player_name.strip()}::{team_name.strip()}"

    for row in dongqiudi_data.get("yellow_rows", []):
        key = key_of(row.get("player_name", ""), row.get("team_name", ""))
        merged[key] = {
            "player_name": row.get("player_name", ""),
            "team_name": row.get("team_name", ""),
            "yellow_cards": row.get("yellow_cards"),
            "red_cards": None,
            "source_notes": ["dongqiudi_legacy_rank"],
        }

    for row in dongqiudi_data.get("red_rows", []):
        key = key_of(row.get("player_name", ""), row.get("team_name", ""))
        if key not in merged:
            merged[key] = {
                "player_name": row.get("player_name", ""),
                "team_name": row.get("team_name", ""),
                "yellow_cards": None,
                "red_cards": row.get("red_cards"),
                "source_notes": ["dongqiudi_legacy_rank"],
            }
        else:
            merged[key]["red_cards"] = row.get("red_cards")
            merged[key]["source_notes"].append("dongqiudi_legacy_rank")

    for row in disciplinary_data.get("events", []):
        # Disciplinary list may not expose team names consistently.
        key = key_of(row.get("player_name", ""), row.get("team_name", ""))
        if key not in merged:
            merged[key] = {
                "player_name": row.get("player_name", ""),
                "team_name": row.get("team_name", ""),
                "yellow_cards": row.get("yellow_cards"),
                "red_cards": row.get("red_cards"),
                "source_notes": ["cfl_disciplinary_title_parse"],
            }
        else:
            if row.get("yellow_cards"):
                merged[key]["yellow_cards"] = (merged[key]["yellow_cards"] or 0) + row["yellow_cards"]
            if row.get("red_cards"):
                merged[key]["red_cards"] = (merged[key]["red_cards"] or 0) + row["red_cards"]
            merged[key]["source_notes"].append("cfl_disciplinary_title_parse")

    rows = list(merged.values())
    rows.sort(key=lambda x: (-(x.get("red_cards") or 0), -(x.get("yellow_cards") or 0), x["player_name"]))
    return rows


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out_path = root / "data" / "csl_cards_raw.json"
    session = build_session()

    dongqiudi_data = fetch_player_cards(session=session, competition="51")
    disciplinary_data = fetch_disciplinary_events(session=session)
    merged_rows = merge_card_rows(dongqiudi_data=dongqiudi_data, disciplinary_data=disciplinary_data)

    payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "dataset": "player_cards",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_url": {
                "dongqiudi_candidates": "http://api.dongqiudi.com/data?competition=51&type={yellow/red candidates}",
                "cfl_disciplinary": disciplinary_data.get("source_url"),
            },
            "errors": dongqiudi_data.get("errors", []) + disciplinary_data.get("errors", []),
        },
        "player_cards": merged_rows,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"cards output written: {out_path}")


if __name__ == "__main__":
    main()
