import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_BASE = "https://api.cfl-china.cn/frontweb/api"
COMPETITION_CODE = "CSL"


class CFLApiEventCrawler:
    def __init__(
        self,
        output_path: Path,
        checkpoint_path: Path,
        season_name: str = "2026",
        finished_only: bool = True,
        chunk_size: int = 10,
        timeout_s: int = 20,
    ) -> None:
        self.output_path = output_path
        self.checkpoint_path = checkpoint_path
        self.season_name = season_name
        self.finished_only = finished_only
        self.chunk_size = max(1, chunk_size)
        self.timeout_s = timeout_s
        self.session = self._build_session()

    def _build_session(self) -> requests.Session:
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
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.cfl-china.cn/",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        return session

    def _get_json(self, url: str) -> Dict[str, Any]:
        resp = self.session.get(url, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json()

    def _load_checkpoint(self) -> Optional[str]:
        if not self.checkpoint_path.exists():
            return None
        with self.checkpoint_path.open("r", encoding="utf-8") as f:
            return json.load(f).get("last_successful_match_id")

    def _save_checkpoint(self, match_id: str) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_successful_match_id": match_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "cfl-api",
        }
        self.checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_existing(self) -> Dict[str, Dict[str, Any]]:
        if not self.output_path.exists():
            return {}
        data = json.loads(self.output_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return {}
        return {str(m.get("match_id")): m for m in data if m.get("match_id")}

    def _save_existing(self, existing_by_id: Dict[str, Dict[str, Any]]) -> None:
        rows = sorted(existing_by_id.values(), key=lambda x: str(x.get("match_id", "")))
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_calendar_id(self) -> str:
        data = self._get_json(f"{API_BASE}/tournaments?competition_code={COMPETITION_CODE}")
        items = data.get("data", {}).get("dataList", [])
        for item in items:
            if str(item.get("name")) == self.season_name:
                return item.get("id", "")
        raise RuntimeError(f"season {self.season_name} calendar_id not found")

    def _get_weeks(self) -> List[int]:
        data = self._get_json(
            f"{API_BASE}/matches/select/week/v2?tournament_calendar_name={self.season_name}"
            f"&competition_code={COMPETITION_CODE}&stage_id="
        )
        weeks = data.get("data", {}).get("weeks", [])
        return [int(w) for w in weeks if str(w).isdigit()]

    def _fetch_matches_for_week(self, calendar_id: str, week: int) -> List[Dict[str, Any]]:
        url = (
            f"{API_BASE}/matches/page?tournament_calendar_id={calendar_id}&competition_code={COMPETITION_CODE}"
            f"&contestant_id=&week={week}&stage_id=&curPage=1&pageSize=999"
        )
        data = self._get_json(url)
        return data.get("data", {}).get("dataList", []) or []

    def _normalize_match_row(self, row: Dict[str, Any], week: int) -> Dict[str, Any]:
        status_raw = str(row.get("match_status") or "")
        status = "finished" if status_raw.lower() == "played" else status_raw.lower() or "scheduled"
        return {
            "match_id": row.get("id"),
            "date": row.get("local_date_time"),
            "home_club": row.get("home_contestant_name"),
            "away_club": row.get("away_contestant_name"),
            "status": status,
            "score": {
                "home": row.get("ft_home_score"),
                "away": row.get("ft_away_score"),
            },
            "round": f"第{week}轮",
            "venue": {
                "name": row.get("venue_short_name") or row.get("venue_long_name"),
                "city": None,
            },
            "detail_path": f"/zh/fixtures/details.html?competition_code={COMPETITION_CODE}&id={row.get('id')}",
        }

    def _goal_player_label(self, item: Dict[str, Any]) -> str:
        cn = str(item.get("player_name") or "").strip()
        en = str(item.get("player_name_en") or "").strip()
        if cn:
            return cn
        if en:
            return en
        return ""

    def _normalize_goal_event(self, item: Dict[str, Any]) -> Dict[str, Any]:
        label = self._goal_player_label(item)
        return {
            "type": "goal",
            "player": label,
            "player_name": label,
            "minute": item.get("time_min"),
        }

    def _normalize_card_event(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        card_type = str(item.get("card_type") or "").upper()
        if card_type == "YC":
            event_type = "yellow_card"
        elif card_type == "RC":
            event_type = "red_card"
        else:
            return None
        cn = str(item.get("player_name") or "").strip()
        en = str(item.get("player_name_en") or "").strip()
        label = cn or en
        return {
            "type": event_type,
            "player": label,
            "player_name": label,
            "minute": item.get("time_min"),
        }

    def _fetch_events(self, match_id: str) -> List[Dict[str, Any]]:
        goals_url = f"{API_BASE}/matches/match/event/goals?match_id={match_id}"
        cards_url = f"{API_BASE}/matches/match/event/cards?match_id={match_id}"
        goals_data = self._get_json(goals_url).get("data", {})
        cards_data = self._get_json(cards_url).get("data", {})

        events: List[Dict[str, Any]] = []
        for side in ("home", "away"):
            for item in goals_data.get(side, []) or []:
                events.append(self._normalize_goal_event(item))
            for item in cards_data.get(side, []) or []:
                norm = self._normalize_card_event(item)
                if norm:
                    events.append(norm)

        events = [e for e in events if e.get("type")]
        events.sort(key=lambda x: x.get("minute") if isinstance(x.get("minute"), int) else 999)
        return events

    def run(self, max_matches: int = 0, reset_checkpoint: bool = False) -> None:
        if reset_checkpoint and self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

        existing_by_id = self._load_existing()
        last_ok = self._load_checkpoint()

        calendar_id = self._get_calendar_id()
        weeks = self._get_weeks()

        all_matches: List[Dict[str, Any]] = []
        for week in weeks:
            rows = self._fetch_matches_for_week(calendar_id, week)
            for row in rows:
                normalized = self._normalize_match_row(row, week)
                if self.finished_only and normalized["status"] != "finished":
                    continue
                all_matches.append(normalized)

        # de-dup by match_id
        dedup = {}
        for m in all_matches:
            dedup[str(m.get("match_id"))] = m
        ordered = sorted(dedup.values(), key=lambda x: str(x.get("match_id")))

        # resume
        start = 0
        if last_ok:
            for i, m in enumerate(ordered):
                if str(m.get("match_id")) == str(last_ok):
                    start = i + 1
                    break
        pending = ordered[start:]
        if max_matches and max_matches > 0:
            pending = pending[:max_matches]

        print(f"season={self.season_name}, finished_only={self.finished_only}, total={len(ordered)}, pending={len(pending)}")
        if not pending:
            self._save_existing(existing_by_id)
            print("nothing to process")
            return

        for i in range(0, len(pending), self.chunk_size):
            chunk = pending[i : i + self.chunk_size]
            print(f"processing chunk {i // self.chunk_size + 1}, size={len(chunk)}")
            for match in chunk:
                match_id = str(match.get("match_id"))
                errors: List[str] = []
                events: List[Dict[str, Any]] = []
                try:
                    events = self._fetch_events(match_id)
                    if not events:
                        errors.append("no_events_from_cfl_api")
                except Exception as exc:
                    errors.append(f"event_fetch_error: {exc}")

                enriched = {
                    **match,
                    "events": events,
                    "errors": errors,
                    "event_scraped_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source": "cfl_api",
                }
                existing_by_id[match_id] = enriched
                self._save_checkpoint(match_id)
                time.sleep(0.25)

            self._save_existing(existing_by_id)
            print(f"checkpoint persisted, output size={len(existing_by_id)}")

        print(f"done. output={self.output_path}")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="CFL API based CSL event crawler")
    parser.add_argument("--output", default=str(root / "data" / "csl_matches_enriched.json"))
    parser.add_argument("--checkpoint", default=str(root / "data" / "cfl_api_event_checkpoint.json"))
    parser.add_argument("--season", default="2026")
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--all-status", action="store_true", help="Include non-finished matches too")
    parser.add_argument("--reset-checkpoint", action="store_true")
    args = parser.parse_args()

    crawler = CFLApiEventCrawler(
        output_path=Path(args.output),
        checkpoint_path=Path(args.checkpoint),
        season_name=args.season,
        finished_only=not args.all_status,
        chunk_size=args.chunk_size,
        timeout_s=args.timeout,
    )
    crawler.run(max_matches=args.max_matches, reset_checkpoint=args.reset_checkpoint)


if __name__ == "__main__":
    main()
