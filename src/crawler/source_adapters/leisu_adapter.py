from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup


LEISU_PLAYER_URL_CANDIDATES = [
    "https://www.leisu.com/data/zuqiu/china/csl/player",
    "https://m.leisu.com/data/zuqiu/china/csl/player",
    "https://www.leisu.com/data/zuqiu/china/super-league/player",
    "https://m.leisu.com/data/zuqiu/china/super-league/player",
]


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _first_text(row: Any, selectors: List[str]) -> str:
    for selector in selectors:
        node = row.select_one(selector)
        if node:
            value = node.get_text(" ", strip=True)
            if value:
                return value
    return ""


def _extract_rows_from_table(table: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    body_rows = table.select("tbody tr") or table.select("tr")
    for tr in body_rows:
        cells = tr.select("td")
        if len(cells) < 3:
            continue

        # Generic fallback extraction by position. Leisu DOM may vary by season/device.
        player_name = _first_text(
            tr,
            [
                "[data-player-name]",
                ".player-name",
                ".name",
                "td:nth-child(2)",
                "td:nth-child(1)",
            ],
        )
        team_name = _first_text(
            tr,
            [
                "[data-team-name]",
                ".team-name",
                ".team",
                "td:nth-child(3)",
            ],
        )
        goals = _to_int(_first_text(tr, [".goal", ".goals", "td[data-col='goals']", "td:nth-child(4)"]))
        assists = _to_int(
            _first_text(tr, [".assist", ".assists", "td[data-col='assists']", "td:nth-child(5)"])
        )
        yellow_cards = _to_int(
            _first_text(tr, [".yellow", ".yellow-card", "td[data-col='yellow']", "td:nth-child(6)"])
        )
        red_cards = _to_int(_first_text(tr, [".red", ".red-card", "td[data-col='red']", "td:nth-child(7)"]))

        if not player_name:
            continue
        rows.append(
            {
                "player_name": player_name,
                "team_name": team_name,
                "goals": goals,
                "assists": assists,
                "yellow_cards": yellow_cards,
                "red_cards": red_cards,
                "source_note": "leisu_player_table",
            }
        )
    return rows


def fetch_player_stats(session: requests.Session) -> Dict[str, Any]:
    errors: List[str] = []
    for url in LEISU_PLAYER_URL_CANDIDATES:
        try:
            resp = session.get(url, timeout=25)
            if resp.status_code != 200:
                errors.append(f"{url} status={resp.status_code}")
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            tables = soup.select("table")
            if not tables:
                errors.append(f"{url} no_table_found")
                continue

            all_rows: List[Dict[str, Any]] = []
            for table in tables:
                all_rows.extend(_extract_rows_from_table(table))

            # De-duplicate by name+team and keep richer row.
            merged: Dict[str, Dict[str, Any]] = {}
            for row in all_rows:
                key = f"{row.get('player_name','').strip()}::{row.get('team_name','').strip()}"
                prev = merged.get(key)
                if prev is None:
                    merged[key] = row
                    continue
                prev_score = sum(int(prev.get(k) is not None) for k in ("goals", "assists", "yellow_cards", "red_cards"))
                cur_score = sum(int(row.get(k) is not None) for k in ("goals", "assists", "yellow_cards", "red_cards"))
                if cur_score > prev_score:
                    merged[key] = row

            if merged:
                rows = sorted(
                    merged.values(),
                    key=lambda x: (
                        -(x.get("goals") or 0),
                        -(x.get("assists") or 0),
                        x.get("player_name") or "",
                    ),
                )
                return {"rows": rows, "source_url": url, "errors": errors}
            errors.append(f"{url} parsed_rows_empty")
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            errors.append(f"{url} exception={exc}")

    return {"rows": [], "source_url": None, "errors": errors}
