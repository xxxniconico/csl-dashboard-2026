"""
Fetch full CSL season fixture list from CFL API (all rounds, all statuses).
Writes JSON only — does not scrape per-match events (fast, safe for schedules).
"""
from __future__ import annotations

import argparse
import json
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
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
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


def _get_json(session: requests.Session, url: str, timeout_s: int) -> Dict[str, Any]:
    resp = session.get(url, timeout=timeout_s)
    resp.raise_for_status()
    return resp.json()


def _calendar_id(session: requests.Session, season_name: str, timeout_s: int) -> str:
    data = _get_json(session, f"{API_BASE}/tournaments?competition_code={COMPETITION_CODE}", timeout_s)
    items = data.get("data", {}).get("dataList", [])
    for item in items:
        if str(item.get("name")) == season_name:
            cid = item.get("id", "")
            if cid:
                return str(cid)
    raise RuntimeError(f"season {season_name!r} calendar_id not found")


def _weeks(session: requests.Session, season_name: str, timeout_s: int) -> List[int]:
    data = _get_json(
        session,
        f"{API_BASE}/matches/select/week/v2?tournament_calendar_name={season_name}"
        f"&competition_code={COMPETITION_CODE}&stage_id=",
        timeout_s,
    )
    raw = data.get("data", {}).get("weeks", []) or []
    out: List[int] = []
    for w in raw:
        try:
            out.append(int(w))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _week_rows(
    session: requests.Session, calendar_id: str, week: int, timeout_s: int
) -> List[Dict[str, Any]]:
    url = (
        f"{API_BASE}/matches/page?tournament_calendar_id={calendar_id}&competition_code={COMPETITION_CODE}"
        f"&contestant_id=&week={week}&stage_id=&curPage=1&pageSize=999"
    )
    data = _get_json(session, url, timeout_s)
    return data.get("data", {}).get("dataList", []) or []


def _normalize_row(row: Dict[str, Any], week: int) -> Dict[str, Any]:
    status_raw = str(row.get("match_status") or "")
    if status_raw.lower() == "played":
        status = "finished"
    elif status_raw.lower() == "fixture":
        status = "scheduled"
    else:
        status = status_raw.lower() or "scheduled"

    home_score = row.get("ft_home_score")
    away_score = row.get("ft_away_score")
    if status != "finished":
        home_score = None
        away_score = None

    venue_name = row.get("venue_short_name") or row.get("venue_long_name")

    return {
        "match_id": row.get("id"),
        "date": row.get("local_date_time"),
        "home_club": str(row.get("home_contestant_name") or "").strip(),
        "away_club": str(row.get("away_contestant_name") or "").strip(),
        "status": status,
        "score": {"home": home_score, "away": away_score},
        "round": f"第{week}轮",
        "venue": {"name": venue_name, "city": None},
        "events": [],
        "source": "cfl_fixtures_api",
    }


def fetch_all_fixtures(season_name: str, timeout_s: int) -> List[Dict[str, Any]]:
    session = _session()
    cal = _calendar_id(session, season_name, timeout_s)
    weeks = _weeks(session, season_name, timeout_s)
    by_id: Dict[str, Dict[str, Any]] = {}
    for week in weeks:
        for row in _week_rows(session, cal, week, timeout_s):
            norm = _normalize_row(row, week)
            mid = str(norm.get("match_id") or "")
            if mid:
                by_id[mid] = norm
    return sorted(by_id.values(), key=lambda x: str(x.get("match_id", "")))


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Fetch full CSL fixture list from CFL API")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument(
        "--output",
        default=str(root / "data" / "csl_season_fixtures_cfl.json"),
    )
    args = parser.parse_args()
    out_path = Path(args.output)
    rows = fetch_all_fixtures(args.season, args.timeout)
    payload = {
        "season": args.season,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "cfl-china.cn matches/page (all weeks)",
        "match_count": len(rows),
        "matches": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} matches -> {out_path}")


if __name__ == "__main__":
    main()
