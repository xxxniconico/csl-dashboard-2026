import json
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup


class CSLCrawler:
    """
    Playwright-based CSL crawler compatible with CrawlerFactory.

    Browser strategy:
    1) Open schedule page in headless Chromium with realistic UA/viewport.
    2) Parse match rows and detail paths from rendered HTML.
    3) Visit each match detail page and extract events (goal/yellow/red) from
       hidden script payloads and visible timeline fragments.
    4) Return standardized match dictionaries.
    """

    SCHEDULE_URL = "http://api.dongqiudi.com/data?competition=51&type=schedule"
    WEB_BASE = "https://www.dongqiudi.com"
    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, timeout: int = 20, headless: bool = True):
        self.timeout = timeout
        self.headless = headless

    def _parse_score(self, score_text: str) -> Dict[str, Any]:
        txt = (score_text or "").strip()
        if ":" in txt:
            parts = txt.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                return {"home": int(parts[0]), "away": int(parts[1]), "status": "finished"}
        return {"home": None, "away": None, "status": "scheduled"}

    def _decode_possible_mojibake(self, text: str) -> str:
        if not text:
            return text
        # Legacy endpoint occasionally looks like UTF-8 bytes decoded as GBK.
        if "�" in text:
            return text
        if re.search(r"[ÃÐÅØ]{2,}", text):
            try:
                return text.encode("latin1").decode("utf-8")
            except Exception:
                return text
        return text

    def _extract_round(self, soup: BeautifulSoup) -> str:
        title = soup.select_one("#schedule_title")
        if title:
            return title.get_text(" ", strip=True)
        return ""

    def _extract_match_path(self, tr: Any) -> str:
        onclick = tr.get("onclick", "")
        # Example: matchinfo_url('/match/analysis/54389808')
        m = re.search(r"matchinfo_url\('([^']+)'\)", onclick)
        return m.group(1) if m else ""

    def _extract_events_from_scripts(self, html: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script")

        # Heuristic 1: detect json-like event blocks in scripts.
        for s in scripts:
            content = s.string or s.get_text() or ""
            if not content:
                continue
            if not any(k in content.lower() for k in ("event", "goal", "yellow", "red", "minute")):
                continue

            # Try a common JSON object pattern around "events".
            match = re.search(r'"events"\s*:\s*(\[[\s\S]*?\])', content)
            if match:
                raw = match.group(1)
                try:
                    parsed = json.loads(raw)
                    for item in parsed:
                        evt = self._normalize_event_item(item)
                        if evt:
                            events.append(evt)
                except Exception:
                    pass

            # Heuristic 2: key-value scan for minute/type/player-like snippets.
            kv_matches = re.findall(
                r'"type"\s*:\s*"([^"]+)".{0,200}?"minute"\s*:\s*"?(\d{1,3})"?'
                r'.{0,200}?"player[^"]*"\s*:\s*"([^"]+)"',
                content,
                flags=re.IGNORECASE | re.DOTALL,
            )
            for evt_type, minute, player in kv_matches:
                norm = self._normalize_event_fields(evt_type, player, minute)
                if norm:
                    events.append(norm)

        # Heuristic 3: parse timeline-like plain text lines.
        if not events:
            page_text = soup.get_text("\n", strip=True)
            line_hits = re.findall(
                r"(?:(\d{1,3})['′]?\s*)?(进球|黄牌|红牌|goal|yellow|red)\s*[:：]?\s*([^\n]{1,30})",
                page_text,
                flags=re.IGNORECASE,
            )
            for minute, evt_type, player in line_hits:
                norm = self._normalize_event_fields(evt_type, player, minute or None)
                if norm:
                    events.append(norm)

        # De-duplicate
        uniq = []
        seen = set()
        for e in events:
            key = (e["type"], e["player"], e["minute"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(e)
        return sorted(uniq, key=lambda x: x.get("minute") or 999)

    def _normalize_event_item(self, item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        evt_type = item.get("type") or item.get("event_type") or item.get("kind")
        minute = item.get("minute") or item.get("time")
        player = item.get("player") or item.get("player_name") or item.get("name")
        return self._normalize_event_fields(evt_type, player, minute)

    def _normalize_event_fields(self, evt_type: Any, player: Any, minute: Any) -> Optional[Dict[str, Any]]:
        if not evt_type:
            return None
        t = str(evt_type).lower()
        if "goal" in t:
            t = "goal"
        elif "yellow" in t or "黄牌" in t:
            t = "yellow_card"
        elif "red" in t or "红牌" in t:
            t = "red_card"
        else:
            return None
        try:
            m = int(str(minute).strip())
        except Exception:
            m = None
        p = str(player).strip() if player is not None else ""
        return {"type": t, "player": p, "minute": m}

    def _human_delay(self, min_s: float = 0.6, max_s: float = 1.7) -> None:
        time.sleep(random.uniform(min_s, max_s))

    def _try_fetch_match_detail(self, page: Any, match_path: str) -> Dict[str, Any]:
        """
        Attempt to fetch match detail page and extract metadata/events.
        On anti-bot redirect, return structured errors.
        """
        detail = {"date": None, "venue": {"name": None, "city": None}, "events": [], "errors": []}
        if not match_path:
            detail["errors"].append("no_match_path")
            return detail

        detail_url = f"{self.WEB_BASE}{match_path}"
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            self._human_delay()
            page.wait_for_load_state("networkidle", timeout=self.timeout * 1000)
            final_url = page.url or detail_url
            if "/live" in final_url:
                detail["errors"].append("redirected_to_live_probable_antibot")
                return detail

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            page_text = soup.get_text(" ", strip=True)

            # Best-effort date extraction.
            date_match = re.search(r"(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})", page_text)
            if date_match:
                detail["date"] = date_match.group(1).replace("/", "-").replace(".", "-")

            # Best-effort venue extraction.
            venue_match = re.search(r"(?:球场|体育场|Stadium)[:：]?\s*([^\s,，。；;]{2,30})", page_text, re.IGNORECASE)
            if venue_match:
                detail["venue"]["name"] = venue_match.group(1)

            detail["events"] = self._extract_events_from_scripts(html)
            if not detail["events"]:
                detail["errors"].append("no_event_payload_found_in_scripts")
            return detail
        except Exception as exc:
            detail["errors"].append(f"detail_fetch_error: {exc}")
            return detail

    def crawl(self) -> List[Dict[str, Any]]:
        matches: List[Dict[str, Any]] = []
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    user_agent=self.DEFAULT_UA,
                    viewport={"width": 1440, "height": 900},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                page = context.new_page()
                page.set_extra_http_headers({"Referer": "https://www.dongqiudi.com/"})
                page.goto(self.SCHEDULE_URL, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                self._human_delay()
                page.wait_for_selector("tr.matchinfo", timeout=self.timeout * 1000)
                page.wait_for_load_state("networkidle", timeout=self.timeout * 1000)

                soup = BeautifulSoup(page.content(), "html.parser")
                round_name = self._extract_round(soup)
                rows = soup.select("tr.matchinfo")

                for idx, tr in enumerate(rows, start=1):
                    tds = tr.select("td")
                    if len(tds) < 4:
                        continue

                    home_club = self._decode_possible_mojibake(tds[1].get_text(" ", strip=True))
                    score_text = tds[2].get_text(" ", strip=True)
                    away_club = self._decode_possible_mojibake(tds[3].get_text(" ", strip=True))
                    score_obj = self._parse_score(score_text)
                    match_path = self._extract_match_path(tr)
                    match_id_match = re.search(r"/(\d+)$", match_path)
                    match_id = match_id_match.group(1) if match_id_match else f"round-{idx}"

                    detail = self._try_fetch_match_detail(page, match_path)
                    errors = []
                    if round_name:
                        errors.append(f"round={round_name}")
                    errors.extend(detail.get("errors", []))

                    matches.append(
                        {
                            "match_id": match_id,
                            "date": detail.get("date"),
                            "venue": detail.get("venue", {"name": None, "city": None}),
                            "home_club": home_club,
                            "away_club": away_club,
                            "status": score_obj["status"],
                            "score": {"home": score_obj["home"], "away": score_obj["away"]},
                            "events": detail.get("events", []),
                            "errors": errors,
                            "crawled_at_utc": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    self._human_delay(0.8, 1.9)

                context.close()
                browser.close()
            return matches
        except ModuleNotFoundError:
            return [
                {
                    "match_id": "playwright_not_installed",
                    "date": None,
                    "venue": {"name": None, "city": None},
                    "home_club": "",
                    "away_club": "",
                    "status": "error",
                    "score": {"home": None, "away": None},
                    "events": [],
                    "errors": [
                        "playwright package is not installed",
                        "run: pip install playwright && python -m playwright install chromium",
                    ],
                }
            ]
        except Exception as exc:
            return [
                {
                    "match_id": "crawler_exception",
                    "date": None,
                    "venue": {"name": None, "city": None},
                    "home_club": "",
                    "away_club": "",
                    "status": "error",
                    "score": {"home": None, "away": None},
                    "events": [],
                    "errors": [f"crawler_exception: {exc}"],
                }
            ]
