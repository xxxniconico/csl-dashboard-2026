import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_URL = "https://www.dongqiudi.com"
SEED_SCHEDULE_URL = "http://api.dongqiudi.com/data?competition=51&type=schedule"


def _parse_score(score_text: str) -> Dict[str, object]:
    txt = (score_text or "").strip()
    if ":" in txt:
        left, right = txt.split(":", 1)
        if left.isdigit() and right.isdigit():
            return {"home": int(left), "away": int(right), "status": "finished"}
    return {"home": None, "away": None, "status": "scheduled"}


def _extract_match_id(match_path: str) -> str:
    m = re.search(r"/(\d+)$", match_path or "")
    return m.group(1) if m else ""


def _extract_match_path(onclick: str) -> str:
    m = re.search(r"matchinfo_url\('([^']+)'\)", onclick or "")
    return m.group(1) if m else ""


def _extract_round_links(soup: BeautifulSoup, current_url: str) -> List[str]:
    links = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if "type=schedule" not in href:
            continue
        full = urljoin(current_url, href)
        if "competition=51" in full:
            links.append(full)
    return links


def _extract_round_name(soup: BeautifulSoup) -> str:
    title = soup.select_one("#schedule_title")
    return title.get_text(" ", strip=True) if title else ""


def _extract_match_date(tr) -> str:
    # Legacy page often has blank first cell for some rows; keep best-effort extraction.
    tds = tr.select("td")
    if not tds:
        return ""
    first_col = tds[0].get_text(" ", strip=True)
    if first_col:
        return first_col
    # Fallback to attributes if present.
    for attr in ("data-time", "data-date", "title"):
        if tr.has_attr(attr):
            return str(tr.get(attr)).strip()
    return ""


def crawl_full_2026_schedule() -> Dict[str, object]:
    matches_by_id: Dict[str, Dict[str, object]] = {}
    round_errors: List[Dict[str, str]] = []
    visited: Set[str] = set()
    queue: List[str] = [SEED_SCHEDULE_URL]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        page.set_extra_http_headers({"Referer": "https://www.dongqiudi.com/"})

        while queue:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector("tr.matchinfo", timeout=15000)
                # networkidle 在动态页面上常多等 10–20s；赛程行已出现即可解析
                try:
                    page.wait_for_load_state("load", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                time.sleep(0.35)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                round_name = _extract_round_name(soup)
                rows = soup.select("tr.matchinfo")

                for tr in rows:
                    onclick = tr.get("onclick", "")
                    match_path = _extract_match_path(onclick)
                    match_id = _extract_match_id(match_path)
                    tds = tr.select("td")
                    if len(tds) < 4 or not match_id:
                        continue

                    home_club = tds[1].get_text(" ", strip=True)
                    score_text = tds[2].get_text(" ", strip=True)
                    away_club = tds[3].get_text(" ", strip=True)
                    score_obj = _parse_score(score_text)
                    date_text = _extract_match_date(tr)

                    matches_by_id[match_id] = {
                        "match_id": match_id,
                        "date": date_text or None,
                        "home_club": home_club,
                        "away_club": away_club,
                        "status": score_obj["status"],
                        "score": {"home": score_obj["home"], "away": score_obj["away"]},
                        "round": round_name,
                        "detail_path": match_path,
                    }

                # discover neighboring rounds (prev / next and any schedule links on page)
                for nxt in _extract_round_links(soup, page.url):
                    if nxt not in visited and nxt not in queue:
                        queue.append(nxt)
            except PlaywrightTimeoutError as exc:
                round_errors.append({"url": url, "error": f"timeout: {exc}"})
            except Exception as exc:
                round_errors.append({"url": url, "error": f"error: {exc}"})

        context.close()
        browser.close()

    ordered = sorted(matches_by_id.values(), key=lambda x: x["match_id"])
    return {
        "matches": ordered,
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "seed_url": SEED_SCHEDULE_URL,
            "round_pages_visited": len(visited),
            "round_errors": round_errors,
            "match_count": len(ordered),
        },
    }


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    output_path = root / "data" / "all_matches_2026_index.json"
    result = crawl_full_2026_schedule()

    # Requirement requests an array of match objects; write matches array only.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result["matches"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {output_path}")
    print(f"matches: {result['meta']['match_count']}, rounds: {result['meta']['round_pages_visited']}")
    if result["meta"]["round_errors"]:
        print(f"round errors: {len(result['meta']['round_errors'])}")
        for item in result["meta"]["round_errors"][:5]:
            print(f" - {item['url']} :: {item['error']}")


if __name__ == "__main__":
    main()
