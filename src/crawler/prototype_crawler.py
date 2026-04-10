import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger("prototype_crawler")

PRIMARY_URL = "https://www.dongqiudi.com/china/csl/standings"
LEGACY_BASE = "http://api.dongqiudi.com/data?competition=51&type={data_type}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.dongqiudi.com/",
    "Connection": "keep-alive",
}


@dataclass
class StandingRow:
    rank: Optional[int]
    team_name: str
    played: Optional[int]
    won: Optional[int]
    drawn: Optional[int]
    lost: Optional[int]
    goals_for: Optional[int]
    goals_against: Optional[int]
    goal_difference: Optional[int]
    points: Optional[int]
    recent_form: List[str]
    source_note: str


@dataclass
class MatchResultRow:
    round_name: str
    home_team: str
    away_team: str
    score: str
    status: str
    source_note: str


@dataclass
class PlayerStatRow:
    rank: Optional[int]
    player_name: str
    team_name: str
    goals: Optional[int]
    assists: Optional[int]
    yellow_cards: Optional[int]
    red_cards: Optional[int]
    source_note: str


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1.0,
        status_forcelist=(403, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    return session


def to_int(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def extract_recent_form(raw: str) -> List[str]:
    # Prototype version: keep only W/D/L letters if present.
    cleaned = raw.replace(" ", "").upper()
    return [c for c in cleaned if c in {"W", "D", "L"}]


def parse_standings_rows(html: str, source_note: str) -> List[StandingRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tr")
    parsed: List[StandingRow] = []

    for row in rows:
        cells = [c.get_text(" ", strip=True) for c in row.select("th,td")]
        if len(cells) < 10:
            continue
        if not cells[0].strip().isdigit():
            continue

        # Expected column order from observed Dongqiudi table:
        # rank, team_name, played, won, drawn, lost, goals_for, goals_against, goal_difference, points
        standing = StandingRow(
            rank=to_int(cells[0]),
            team_name=cells[1].strip(),
            played=to_int(cells[2]),
            won=to_int(cells[3]),
            drawn=to_int(cells[4]),
            lost=to_int(cells[5]),
            goals_for=to_int(cells[6]),
            goals_against=to_int(cells[7]),
            goal_difference=to_int(cells[8]),
            points=to_int(cells[9]),
            recent_form=extract_recent_form(cells[10]) if len(cells) > 10 else [],
            source_note=source_note,
        )
        parsed.append(standing)

    return parsed


def parse_player_rank_rows(html: str, metric: str, source_note: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for row in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in row.select("th,td")]
        if len(cells) < 4 or not cells[0].strip().isdigit():
            continue
        rows.append(
            {
                "rank": to_int(cells[0]),
                "player_name": cells[1].strip(),
                "team_name": cells[2].strip(),
                metric: to_int(cells[3]),
            }
        )
    return rows


def parse_schedule_rows(html: str, source_note: str) -> List[MatchResultRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tr")
    parsed: List[MatchResultRow] = []
    round_name = ""

    if rows:
        round_cells = [c.get_text(" ", strip=True) for c in rows[0].select("th,td")]
        if round_cells:
            round_name = round_cells[1] if len(round_cells) > 1 else round_cells[0]

    for row in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in row.select("th,td")]
        if len(cells) < 4:
            continue
        score = cells[2].strip()
        status = "finished" if ":" in score and any(ch.isdigit() for ch in score) else "scheduled"
        parsed.append(
            MatchResultRow(
                round_name=round_name,
                home_team=cells[1].strip(),
                away_team=cells[3].strip(),
                score=score,
                status=status,
                source_note=source_note,
            )
        )
    return parsed


def fetch_html(session: requests.Session, url: str, timeout: int = 25) -> requests.Response:
    LOGGER.info("Requesting URL: %s", url)
    response = session.get(url, timeout=timeout)
    LOGGER.info("Response status: %s for %s", response.status_code, url)
    return response


def decode_html_with_fallback(response: requests.Response) -> str:
    html_text = response.text
    if "����" in html_text:
        for enc in ("utf-8", "gb18030", "gbk"):
            try:
                html_text = response.content.decode(enc, errors="replace")
                if "table" in html_text.lower():
                    break
            except Exception:
                continue
    return html_text


def fetch_legacy_html(session: requests.Session, data_type: str) -> str:
    url = LEGACY_BASE.format(data_type=data_type)
    response = fetch_html(session, url)
    if response.status_code != 200:
        raise RuntimeError(f"{data_type} endpoint returned status {response.status_code}")
    return decode_html_with_fallback(response)


def collect_standings(session: requests.Session) -> dict:
    errors: List[str] = []

    # Attempt 1: official standings page (likely blocked in non-browser context).
    try:
        resp = fetch_html(session, PRIMARY_URL)
        if resp.status_code == 200 and "<table" in resp.text.lower():
            standings = parse_standings_rows(resp.text, "primary_web_page")
            if standings:
                return {"rows": standings, "chosen_source": PRIMARY_URL, "errors": errors}
        errors.append(f"primary_failed_status_{resp.status_code}")
    except Exception as exc:  # pragma: no cover - runtime/network dependent
        msg = f"primary_exception: {exc}"
        LOGGER.exception(msg)
        errors.append(msg)

    html_text = fetch_legacy_html(session, "team_rank")
    standings = parse_standings_rows(html_text, "legacy_data_endpoint")
    if not standings:
        raise RuntimeError("no standings rows parsed from fallback endpoint")

    return {
        "rows": standings,
        "chosen_source": LEGACY_BASE.format(data_type="team_rank"),
        "errors": errors,
    }


def collect_match_results(session: requests.Session) -> dict:
    html_text = fetch_legacy_html(session, "schedule")
    rows = parse_schedule_rows(html_text, "legacy_data_endpoint")
    return {
        "rows": rows,
        "chosen_source": LEGACY_BASE.format(data_type="schedule"),
        "errors": [],
    }


def collect_player_stats(session: requests.Session) -> dict:
    errors: List[str] = []
    goals = parse_player_rank_rows(fetch_legacy_html(session, "goal_rank"), "goals", "legacy_data_endpoint")
    assists = parse_player_rank_rows(
        fetch_legacy_html(session, "assist_rank"), "assists", "legacy_data_endpoint"
    )

    merged: Dict[str, PlayerStatRow] = {}
    for row in goals:
        key = f"{row['player_name']}::{row['team_name']}"
        merged[key] = PlayerStatRow(
            rank=row["rank"],
            player_name=row["player_name"],
            team_name=row["team_name"],
            goals=row.get("goals"),
            assists=None,
            yellow_cards=None,
            red_cards=None,
            source_note="legacy_data_endpoint",
        )

    for row in assists:
        key = f"{row['player_name']}::{row['team_name']}"
        if key not in merged:
            merged[key] = PlayerStatRow(
                rank=row["rank"],
                player_name=row["player_name"],
                team_name=row["team_name"],
                goals=None,
                assists=row.get("assists"),
                yellow_cards=None,
                red_cards=None,
                source_note="legacy_data_endpoint",
            )
        else:
            merged[key].assists = row.get("assists")

    # Current public legacy endpoints do not expose yellow/red ranking tables for CSL.
    errors.append("yellow/red card ranking endpoints returned empty tables")

    rows = sorted(
        merged.values(),
        key=lambda x: (
            -(x.goals or 0),
            -(x.assists or 0),
            x.player_name,
        ),
    )
    return {
        "rows": rows,
        "chosen_source": {
            "goals": LEGACY_BASE.format(data_type="goal_rank"),
            "assists": LEGACY_BASE.format(data_type="assist_rank"),
        },
        "errors": errors,
    }


def save_payload(output_path: Path, payload: dict, data_key: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    LOGGER.info("Saved %s rows to %s", len(payload.get(data_key, [])), output_path)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    start = time.time()

    root_dir = Path(__file__).resolve().parents[2]
    session = build_session()

    standings_result = collect_standings(session)
    standings_payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset": "standings",
            "source_url": standings_result["chosen_source"],
            "notes": (
                "Primary standings URL can return 403 to scripted requests. "
                "This prototype falls back to the legacy data endpoint."
            ),
            "errors": standings_result["errors"],
        },
        "standings": [asdict(row) for row in standings_result["rows"]],
    }
    save_payload(root_dir / "data" / "csl_standings_raw.json", standings_payload, "standings")

    match_result = collect_match_results(session)
    matches_payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset": "match_results",
            "source_url": match_result["chosen_source"],
            "errors": match_result["errors"],
        },
        "match_results": [asdict(row) for row in match_result["rows"]],
    }
    save_payload(root_dir / "data" / "csl_match_results_raw.json", matches_payload, "match_results")

    player_result = collect_player_stats(session)
    players_payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset": "player_stats",
            "source_url": player_result["chosen_source"],
            "errors": player_result["errors"],
        },
        "player_stats": [asdict(row) for row in player_result["rows"]],
    }
    save_payload(root_dir / "data" / "csl_player_stats_raw.json", players_payload, "player_stats")

    LOGGER.info("Done in %.2fs", time.time() - start)


if __name__ == "__main__":
    main()
