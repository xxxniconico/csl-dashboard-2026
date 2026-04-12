"""
中足联 CFL 公开 API：拉取赛季注册球员列表（players/page），写入 data/cfl_players_page_raw.json。

用于仪表盘展示球员档案（位置、号码、身高体重、国籍、头像等），与射手/事件统计互补。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_BASE = "https://api.cfl-china.cn/frontweb/api"
COMPETITION_CODE = "CSL"


def _session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.cfl-china.cn/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return session


def fetch_all_players(
    session: requests.Session,
    season_name: str,
    page_size: int,
    timeout_s: float,
) -> List[Dict[str, Any]]:
    page = 1
    out: List[Dict[str, Any]] = []
    total_expected: int | None = None

    while True:
        url = (
            f"{API_BASE}/players/page?curPage={page}&pageSize={page_size}"
            f"&competition_code={COMPETITION_CODE}&tournament_calendar_name={season_name}"
        )
        resp = session.get(url, timeout=timeout_s)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        batch = data.get("dataList") if isinstance(data.get("dataList"), list) else []
        if total_expected is None:
            try:
                total_expected = int(data.get("count") or 0)
            except (TypeError, ValueError):
                total_expected = None

        if not batch:
            break
        out.extend([x for x in batch if isinstance(x, dict)])
        if total_expected is not None and len(out) >= total_expected:
            break
        if len(batch) < page_size:
            break
        page += 1
        if page > 500:
            break

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch CFL /players/page roster into data/")
    parser.add_argument(
        "--season",
        default="2026",
        help="对应 tournament_calendar_name（赛季名称）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出 JSON 路径（默认 data/cfl_players_page_raw.json）",
    )
    parser.add_argument("--page-size", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    out_path = args.output or (root / "data" / "cfl_players_page_raw.json")

    session = _session()
    rows = fetch_all_players(session, args.season, max(10, min(args.page_size, 200)), args.timeout)

    payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "source": "cfl_players_page",
            "api": f"{API_BASE}/players/page",
            "competition_code": COMPETITION_CODE,
            "tournament_calendar_name": args.season,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "count": len(rows),
        },
        "players": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {len(rows)} players -> {out_path}")


if __name__ == "__main__":
    main()
    sys.exit(0)
