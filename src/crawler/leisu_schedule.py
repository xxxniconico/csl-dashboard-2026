"""
雷速体育 CSL 赛程索引：用 Playwright 打开页面并拦截 JSON 响应（站点有 WAF，纯 HTTP 不可用）。
从任意嵌套结构中启发式提取「主客队 + 比赛 id」条目，并生成 batch_event_crawler 可用的 detail_path。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LEISU_REFERER = "https://www.leisu.com/"
# 依次尝试；任一页面触发 XHR 即可填充赛程 JSON
SEED_URLS = [
    "https://m.leisu.com/data/zuqiu/china/csl",
    "https://www.leisu.com/data/zuqiu/china/csl",
    "https://m.leisu.com/score/pc",
    "https://www.leisu.com/data/zuqiu/china/super-league",
]


def _norm(s: Any) -> str:
    return str(s or "").strip()


def _pick_team_name(obj: Any, *keys: str) -> str:
    if isinstance(obj, str):
        return _norm(obj)
    if not isinstance(obj, dict):
        return ""
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and _norm(v):
            return _norm(v)
        if isinstance(v, dict):
            for sub in ("name", "team_name", "teamName", "title", "short_name"):
                t = _norm(v.get(sub))
                if t:
                    return t
    return ""


def _extract_home_away(d: Dict[str, Any]) -> Tuple[str, str]:
    """从单条疑似比赛 dict 中取主客队名称。"""
    home = (
        _pick_team_name(d.get("home_team"), "name", "team_name")
        or _pick_team_name(d.get("homeTeam"))
        or _pick_team_name(d.get("home"))
        or _norm(d.get("home_team_name"))
        or _norm(d.get("homeName"))
        or _norm(d.get("home_name"))
    )
    away = (
        _pick_team_name(d.get("away_team"), "name", "team_name")
        or _pick_team_name(d.get("awayTeam"))
        or _pick_team_name(d.get("away"))
        or _norm(d.get("away_team_name"))
        or _norm(d.get("awayName"))
        or _norm(d.get("away_name"))
    )
    if not home and isinstance(d.get("home"), str):
        home = _norm(d.get("home"))
    if not away and isinstance(d.get("away"), str):
        away = _norm(d.get("away"))
    return home, away


def _pick_match_id(d: Dict[str, Any]) -> str:
    for key in ("match_id", "matchId", "mid", "fid", "id", "game_id", "gameId"):
        v = d.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s.isdigit() and len(s) >= 6:
            return s
    return ""


def _pick_time(d: Dict[str, Any]) -> str:
    for key in (
        "match_time",
        "matchTime",
        "start_time",
        "startTime",
        "time",
        "date",
        "match_date",
        "local_date_time",
    ):
        v = d.get(key)
        if v is None:
            continue
        t = _norm(v)
        if t and re.search(r"20\d{2}", t):
            return t[:16] if len(t) >= 16 else t
    return ""


def _pick_score(d: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], str]:
    sc = d.get("score")
    if isinstance(sc, dict):
        h, a = sc.get("home"), sc.get("away")
        try:
            if h is not None and a is not None:
                hi, ai = int(h), int(a)
                return hi, ai, "finished"
        except (TypeError, ValueError):
            pass
    for key in ("home_score", "away_score", "homeScore", "awayScore"):
        if key in d:
            pass
    hs = d.get("home_score")
    aws = d.get("away_score")
    try:
        if hs is not None and aws is not None:
            return int(hs), int(aws), "finished"
    except (TypeError, ValueError):
        pass
    st = _norm(d.get("status") or d.get("state") or d.get("match_status")).lower()
    if st in ("finished", "played", "end", "closed"):
        return None, None, "finished"
    return None, None, "scheduled"


def _match_dict_to_row(d: Dict[str, Any], round_guess: str) -> Optional[Dict[str, Any]]:
    mid = _pick_match_id(d)
    home, away = _extract_home_away(d)
    if not mid or not home or not away:
        return None
    if len(home) < 2 or len(away) < 2:
        return None
    if not re.search(r"[\u4e00-\u9fff]", home + away):
        return None
    h_s, a_s, st = _pick_score(d)
    status = st
    if h_s is not None and a_s is not None:
        status = "finished"
    time_s = _pick_time(d)
    return {
        "match_id": mid,
        "date": time_s or None,
        "home_club": home,
        "away_club": away,
        "status": status,
        "score": {"home": h_s, "away": a_s},
        "round": _norm(d.get("round")) or _norm(d.get("round_name")) or round_guess,
        "detail_path": f"/score/pc/detail?match_id={mid}",
        "source": "leisu_schedule",
    }


def walk_json_for_matches(obj: Any, sink: List[Dict[str, Any]], round_guess: str = "") -> None:
    if isinstance(obj, dict):
        row = _match_dict_to_row(obj, round_guess)
        if row:
            sink.append(row)
        for v in obj.values():
            walk_json_for_matches(v, sink, round_guess)
    elif isinstance(obj, list):
        for it in obj:
            walk_json_for_matches(it, sink, round_guess)


def crawl_leisu_csl_index() -> Dict[str, Any]:
    json_rows: List[Dict[str, Any]] = []
    page_errors: List[Dict[str, str]] = []

    def on_response(resp) -> None:
        try:
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct.lower():
                return
            url = resp.url or ""
            if "leisu" not in url.lower():
                return
            text = resp.text()
            if not text or len(text) < 20:
                return
            payload = json.loads(text)
            walk_json_for_matches(payload, json_rows, "")
        except Exception:
            return

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
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "font", "media"}
            else route.continue_(),
        )
        page = context.new_page()
        page.on("response", on_response)
        page.set_extra_http_headers({"Referer": LEISU_REFERER})

        for url in SEED_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("load", timeout=20000)
                except PlaywrightTimeoutError:
                    pass
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(1800)
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(1200)
            except Exception as exc:
                page_errors.append({"url": url, "error": str(exc)})

        context.close()
        browser.close()

    # 合并：按 match_id 去重，优先保留有队名与比分的记录
    by_id: Dict[str, Dict[str, Any]] = {}

    def score_row(r: Dict[str, Any]) -> Tuple[int, int, int]:
        has_names = 1 if (r.get("home_club") and r.get("away_club")) else 0
        sc = r.get("score") if isinstance(r.get("score"), dict) else {}
        has_score = 1 if sc and sc.get("home") is not None and sc.get("away") is not None else 0
        src = 1 if r.get("source") == "leisu_schedule" else 0
        return (has_names, has_score, src)

    for r in json_rows:
        mid = str(r.get("match_id") or "")
        if not mid:
            continue
        prev = by_id.get(mid)
        if prev is None or score_row(r) > score_row(prev):
            by_id[mid] = r

    ordered = sorted(
        (r for r in by_id.values() if _norm(r.get("home_club")) and _norm(r.get("away_club"))),
        key=lambda x: str(x.get("match_id", "")),
    )
    return {
        "matches": ordered,
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "leisu",
            "seed_urls": SEED_URLS,
            "page_errors": page_errors,
            "match_count": len(ordered),
            "raw_hits_before_dedup": len(json_rows),
        },
    }
