import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from source_adapters.leisu_playwright_adapter import fetch_player_stats_playwright
from source_adapters.leisu_adapter import fetch_player_stats


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.leisu.com/",
        }
    )
    return session


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out_path = root / "data" / "csl_player_stats_leisu_raw.json"

    session = build_session()
    result = fetch_player_stats(session)
    strategy = "requests_html"
    if not result.get("rows"):
        fallback = fetch_player_stats_playwright()
        result["errors"] = (result.get("errors", []) or []) + (fallback.get("errors", []) or [])
        if fallback.get("rows"):
            result["rows"] = fallback["rows"]
            result["source_url"] = fallback.get("source_url")
            strategy = "playwright_rendered_html"
        else:
            strategy = "playwright_fallback_failed"

    payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "dataset": "player_stats_supplement",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_url": result.get("source_url"),
            "strategy": strategy,
            "errors": result.get("errors", []),
            "notes": [
                "Leisu source is used as a supplement when primary source misses player name/stats.",
                "Parser is resilient to DOM changes and may return partial columns.",
                "When requests parsing fails, Playwright browser rendering is used as fallback.",
            ],
        },
        "player_stats": result.get("rows", []),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"player stats supplement written: {out_path}")
    print(f"rows: {len(payload['player_stats'])}")


if __name__ == "__main__":
    main()
