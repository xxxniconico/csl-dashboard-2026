from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from source_adapters.leisu_adapter import LEISU_PLAYER_URL_CANDIDATES


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


def _extract_from_table_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: List[Dict[str, Any]] = []
    for tr in soup.select("table tbody tr") or soup.select("table tr"):
        cells = tr.select("td")
        if len(cells) < 3:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        if not any(texts):
            continue

        player_name = ""
        team_name = ""
        goals = assists = yellow_cards = red_cards = None

        # Heuristic extraction robust against column order drift.
        for t in texts:
            if not player_name and re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", t) and not t.isdigit():
                player_name = t
                continue
            if player_name and not team_name and re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", t) and not t.isdigit():
                team_name = t
                continue

        numeric = [_to_int(t) for t in texts]
        numeric = [x for x in numeric if x is not None]
        if numeric:
            goals = numeric[0] if len(numeric) > 0 else None
            assists = numeric[1] if len(numeric) > 1 else None
            yellow_cards = numeric[2] if len(numeric) > 2 else None
            red_cards = numeric[3] if len(numeric) > 3 else None

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
                "source_note": "leisu_playwright_table",
            }
        )
    return rows


def _extract_from_script_json(html: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for match in re.finditer(r"(\{.*?(?:player|shooter|assist|yellow|red).*?\})", html, flags=re.I | re.S):
        snippet = match.group(1)
        try:
            payload = json.loads(snippet)
        except Exception:
            continue
        stack = [payload]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                name = str(
                    cur.get("playerName")
                    or cur.get("player_name")
                    or cur.get("name")
                    or cur.get("shooter")
                    or ""
                ).strip()
                if name:
                    rows.append(
                        {
                            "player_name": name,
                            "team_name": str(cur.get("teamName") or cur.get("team_name") or "").strip(),
                            "goals": _to_int(cur.get("goal") or cur.get("goals")),
                            "assists": _to_int(cur.get("assist") or cur.get("assists")),
                            "yellow_cards": _to_int(cur.get("yellow") or cur.get("yellow_cards")),
                            "red_cards": _to_int(cur.get("red") or cur.get("red_cards")),
                            "source_note": "leisu_playwright_script_json",
                        }
                    )
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
    return rows


def _extract_from_json_payload(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            name = str(
                cur.get("playerName")
                or cur.get("player_name")
                or cur.get("name")
                or cur.get("shooter")
                or ""
            ).strip()
            has_metric_key = any(
                k in cur
                for k in (
                    "goal",
                    "goals",
                    "assist",
                    "assists",
                    "yellow",
                    "yellow_cards",
                    "red",
                    "red_cards",
                )
            )
            if name and has_metric_key:
                rows.append(
                    {
                        "player_name": name,
                        "team_name": str(cur.get("teamName") or cur.get("team_name") or "").strip(),
                        "goals": _to_int(cur.get("goal") or cur.get("goals")),
                        "assists": _to_int(cur.get("assist") or cur.get("assists")),
                        "yellow_cards": _to_int(cur.get("yellow") or cur.get("yellow_cards")),
                        "red_cards": _to_int(cur.get("red") or cur.get("red_cards")),
                        "source_note": "leisu_playwright_xhr_json",
                    }
                )
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return rows


def _dedup(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('player_name','').strip()}::{row.get('team_name','').strip()}"
        if not row.get("player_name"):
            continue
        prev = merged.get(key)
        if prev is None:
            merged[key] = row
            continue
        prev_score = sum(int(prev.get(k) is not None) for k in ("goals", "assists", "yellow_cards", "red_cards"))
        cur_score = sum(int(row.get(k) is not None) for k in ("goals", "assists", "yellow_cards", "red_cards"))
        if cur_score > prev_score:
            merged[key] = row
    return sorted(
        merged.values(),
        key=lambda x: (-(x.get("goals") or 0), -(x.get("assists") or 0), x.get("player_name") or ""),
    )


def fetch_player_stats_playwright() -> Dict[str, Any]:
    errors: List[str] = []
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"rows": [], "source_url": None, "errors": [f"playwright_import_error={exc}"]}

    storage_path = Path(__file__).resolve().parents[3] / "data" / "leisu_storage_state.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context_kwargs = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
        }
        if storage_path.exists():
            context_kwargs["storage_state"] = str(storage_path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        captured_rows: List[Dict[str, Any]] = []

        def on_response(resp: Any) -> None:
            try:
                ctype = (resp.headers or {}).get("content-type", "")
                url = resp.url or ""
                if "json" in ctype.lower() or any(x in url.lower() for x in ("player", "shooter", "assist", "rank")):
                    text = resp.text()
                    try:
                        payload = json.loads(text)
                    except Exception:
                        return
                    captured_rows.extend(_extract_from_json_payload(payload))
            except Exception:
                return

        page.on("response", on_response)

        for url in LEISU_PLAYER_URL_CANDIDATES:
            try:
                captured_rows.clear()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                page.mouse.wheel(0, 1600)
                time.sleep(1.2)
                html = page.content()
                rows = _extract_from_table_html(html)
                if not rows:
                    rows = _extract_from_script_json(html)
                if not rows and captured_rows:
                    rows = list(captured_rows)
                rows = _dedup(rows)
                if rows:
                    storage_path.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(storage_path))
                    context.close()
                    browser.close()
                    return {"rows": rows, "source_url": url, "errors": errors}
                errors.append(f"{url} parsed_rows_empty_in_browser")
            except Exception as exc:
                errors.append(f"{url} browser_exception={exc}")

        storage_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(storage_path))
        context.close()
        browser.close()

    return {"rows": [], "source_url": None, "errors": errors}
