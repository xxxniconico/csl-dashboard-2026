"""
从懂球帝 legacy 接口拉取射手榜 + 助攻榜，写入 data/csl_player_stats_raw.json。

与 prototype_crawler.collect_player_stats 同源逻辑，便于在 CI 中单独执行而无需跑完整 prototype。
"""
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

_CRAWLER_DIR = Path(__file__).resolve().parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from prototype_crawler import (  # noqa: E402
    build_session,
    collect_player_stats,
    save_payload,
)

ROOT = Path(__file__).resolve().parents[2]

LOGGER = logging.getLogger("csl_player_rank_api_crawler")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    start = time.time()
    session = build_session()
    player_result = collect_player_stats(session)
    players_payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset": "player_stats",
            "source_url": player_result["chosen_source"],
            "errors": player_result["errors"],
            "notes": [
                "Merged from Dongqiudi legacy goal_rank and assist_rank HTML tables.",
            ],
        },
        "player_stats": [asdict(row) for row in player_result["rows"]],
    }
    out = ROOT / "data" / "csl_player_stats_raw.json"
    n = len(players_payload.get("player_stats", []))
    if n == 0:
        LOGGER.warning(
            "No player rows parsed from goal_rank/assist_rank (endpoint may have changed or returned an error page)."
        )
    save_payload(out, players_payload, "player_stats")
    LOGGER.info("Done in %.2fs", time.time() - start)


if __name__ == "__main__":
    main()
