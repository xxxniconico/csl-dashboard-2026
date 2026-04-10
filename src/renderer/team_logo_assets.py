"""Fetch CSL team crests from CFL China API and cache under web/assets/team_logos/."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_BASE = "https://api.cfl-china.cn/frontweb/api"
COMPETITION_CODE = "CSL"
DEFAULT_SEASON = "2026"


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
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


def _normalize_icon_url(raw: Any) -> str:
    if not raw:
        return ""
    url = str(raw).strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def _get_calendar_id(session: requests.Session, season_name: str) -> str:
    data = session.get(
        f"{API_BASE}/tournaments?competition_code={COMPETITION_CODE}", timeout=25
    )
    data.raise_for_status()
    payload = data.json()
    items = payload.get("data", {}).get("dataList", []) or []
    for item in items:
        if str(item.get("name")) == season_name:
            cid = str(item.get("id") or "").strip()
            if cid:
                return cid
    raise RuntimeError(f"calendar id not found for season={season_name}")


def _get_weeks(session: requests.Session, season_name: str) -> List[int]:
    url = (
        f"{API_BASE}/matches/select/week/v2"
        f"?tournament_calendar_name={season_name}"
        f"&competition_code={COMPETITION_CODE}&stage_id="
    )
    resp = session.get(url, timeout=25)
    resp.raise_for_status()
    weeks = resp.json().get("data", {}).get("weeks", []) or []
    out: List[int] = []
    for w in weeks:
        try:
            out.append(int(w))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def fetch_club_icon_urls(
    session: requests.Session, calendar_id: str, weeks: List[int]
) -> Dict[str, str]:
    """club_name -> absolute icon URL (first wins)."""
    mapping: Dict[str, str] = {}
    for week in weeks:
        url = (
            f"{API_BASE}/matches/page"
            f"?tournament_calendar_id={calendar_id}"
            f"&competition_code={COMPETITION_CODE}"
            f"&contestant_id=&week={week}&stage_id=&curPage=1&pageSize=999"
        )
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("dataList", []) or []
        for row in rows:
            pairs = (
                (row.get("home_contestant_name"), row.get("home_contestant_icon")),
                (row.get("away_contestant_name"), row.get("away_contestant_icon")),
            )
            for name, icon in pairs:
                club = str(name or "").strip()
                if not club or club in mapping:
                    continue
                u = _normalize_icon_url(icon)
                if u:
                    mapping[club] = u
    return mapping


def download_team_logos(
    root: Path,
    club_names: Set[str],
    url_by_club: Dict[str, str],
    session: requests.Session,
) -> Dict[str, str]:
    """
    Download icons for clubs in club_names; return club_name -> path relative to web/index.html.
    """
    assets_dir = root / "web" / "assets" / "team_logos"
    assets_dir.mkdir(parents=True, exist_ok=True)
    meta_path = assets_dir / "_meta.json"
    meta: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    result: Dict[str, str] = {}
    for name in sorted(club_names):
        url = url_by_club.get(name)
        if not url:
            continue
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
            ext = ".png"
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]
        filename = f"{digest}{ext}"
        rel = f"assets/team_logos/{filename}"
        dest = assets_dir / filename

        prev_url = (meta.get("files") or {}).get(filename)
        if dest.exists() and prev_url == url:
            result[name] = rel
            continue
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            dest.write_bytes(r.content)
            if "files" not in meta:
                meta["files"] = {}
            meta["files"][filename] = url
            result[name] = rel
        except Exception:
            continue

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def build_team_logo_map_for_dashboard(
    root: Path,
    club_names: Set[str],
    season_name: str = DEFAULT_SEASON,
) -> Dict[str, str]:
    """
    Pull icon URLs from CFL API for the season, download assets for requested club names.
    Returns mapping club_name -> relative URL for <img src>.
    """
    if not club_names:
        return {}
    session = _build_session()
    try:
        calendar_id = _get_calendar_id(session, season_name)
        weeks = _get_weeks(session, season_name)
        if not weeks:
            return {}
        url_by_club = fetch_club_icon_urls(session, calendar_id, weeks)
        return download_team_logos(root, club_names, url_by_club, session)
    except Exception:
        return {}
