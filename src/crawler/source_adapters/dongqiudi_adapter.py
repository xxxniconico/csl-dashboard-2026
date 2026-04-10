from typing import List, Optional

import requests
from bs4 import BeautifulSoup


LEGACY_BASE = "http://api.dongqiudi.com/data?competition={competition}&type={data_type}"


def to_int(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def fetch_legacy_html(
    session: requests.Session,
    competition: str,
    data_type: str,
) -> str:
    url = LEGACY_BASE.format(competition=competition, data_type=data_type)
    response = session.get(url, timeout=20)
    response.raise_for_status()
    html = response.text
    if "����" in html:
        for enc in ("utf-8", "gb18030", "gbk"):
            try:
                html = response.content.decode(enc, errors="replace")
                if "table" in html.lower():
                    break
            except Exception:
                continue
    return html


def parse_rank_table(html: str, value_key: str) -> List[dict]:
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
                value_key: to_int(cells[3]),
            }
        )
    return rows


def fetch_player_cards(
    session: requests.Session,
    competition: str = "51",
) -> dict:
    # Candidate endpoints observed on the legacy site.
    yellow_candidates = ["yellow_rank", "yellow_card_rank", "yellowcard_rank"]
    red_candidates = ["red_rank", "red_card_rank", "redcard_rank"]
    errors = []

    def collect_first_nonempty(candidates: List[str], key: str) -> List[dict]:
        for data_type in candidates:
            try:
                html = fetch_legacy_html(session, competition=competition, data_type=data_type)
                parsed = parse_rank_table(html, key)
                if parsed:
                    return parsed
            except Exception as exc:
                errors.append(f"{data_type}: {exc}")
        return []

    yellow_rows = collect_first_nonempty(yellow_candidates, "yellow_cards")
    red_rows = collect_first_nonempty(red_candidates, "red_cards")

    if not yellow_rows:
        errors.append("no yellow-card ranking table found on tested Dongqiudi endpoints")
    if not red_rows:
        errors.append("no red-card ranking table found on tested Dongqiudi endpoints")

    return {"yellow_rows": yellow_rows, "red_rows": red_rows, "errors": errors}
