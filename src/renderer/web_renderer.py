import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

_RENDERER_DIR = Path(__file__).resolve().parent
if str(_RENDERER_DIR) not in sys.path:
    sys.path.insert(0, str(_RENDERER_DIR))

from team_logo_assets import build_team_logo_map_for_dashboard

_SRC_ROOT = Path(__file__).resolve().parent.parent
_PROC = _SRC_ROOT / "processor"
if str(_PROC) not in sys.path:
    sys.path.insert(0, str(_PROC))
from event_names import resolve_event_player_name
from player_team_utils import (
    collect_ranking_player_rows,
    exact_team_lookup_from_rows,
    fuzzy_team_substring_match,
)


def apply_team_logo_public_base(logos: Dict[str, str], base: str) -> Dict[str, str]:
    """
    Prefix relative logo paths for hosting under a subpath or absolute site root.
    Set env CSL_DASHBOARD_PUBLIC_BASE, e.g. https://example.com/myapp or /myapp
    """
    b = (base or "").strip().rstrip("/")
    if not b:
        return logos
    out: Dict[str, str] = {}
    for name, rel in logos.items():
        r = str(rel or "").strip()
        if not r or r.startswith(("http://", "https://", "//")):
            out[name] = r
            continue
        path = r.lstrip("/")
        if b.startswith("http://") or b.startswith("https://"):
            out[name] = f"{b}/{path}"
        else:
            root = b if b.startswith("/") else f"/{b.lstrip('/')}"
            out[name] = f"{root}/{path}"
    return out


def load_data(root: Path) -> Dict[str, Any]:
    candidates = [
        root / "data" / "all_seasons_unified_ext_enriched.json",
        root / "data" / "csl_final_production_ready.json",
        root / "data" / "all_seasons_unified_index.json",
    ]
    for p in candidates:
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data["_source_file"] = p.name
            return data
    raise FileNotFoundError("No unified dataset found in data directory.")


def _norm_name(name: Any) -> str:
    return str(name or "").strip()


def _is_synthetic_player_label(name: str) -> bool:
    """Placeholder names from data_enricher (e.g. 上海海港球员3) or empty."""
    n = _norm_name(name)
    if not n or n == "Unknown":
        return True
    if re.search(r"球员\d+$", n):
        return True
    if n.startswith("未命名球员"):
        return True
    return False


def _event_merge_rank(events: Any) -> Tuple[int, int, int, int]:
    """
    Sort key for picking the better match copy: higher is better.
    (real_named_events, -synthetic_named, total_with_any_name, total_events)
    """
    if not isinstance(events, list):
        return (0, 0, 0, 0)
    real = synth = empty = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        p = resolve_event_player_name(event) or _norm_name(event.get("player") or event.get("player_name"))
        if not p:
            empty += 1
        elif _is_synthetic_player_label(p):
            synth += 1
        else:
            real += 1
    total = len([e for e in events if isinstance(e, dict)])
    named = real + synth
    return (real, -synth, named, total)


def _pick_richer_match(prev: Dict[str, Any], cur: Dict[str, Any]) -> Dict[str, Any]:
    rp = _event_merge_rank(prev.get("events"))
    rc = _event_merge_rank(cur.get("events"))
    if rc > rp:
        return cur
    if rc < rp:
        return prev
    # Tie on event quality: prefer explicit CFL API source, then longer id (CFL ids are opaque strings).
    src_prev = str(prev.get("source") or "")
    src_cur = str(cur.get("source") or "")
    if src_cur == "cfl_api" and src_prev != "cfl_api":
        return cur
    if src_prev == "cfl_api" and src_cur != "cfl_api":
        return prev
    lid = len(str(prev.get("match_id") or "")), len(str(cur.get("match_id") or ""))
    if lid[1] != lid[0]:
        return cur if lid[1] > lid[0] else prev
    return prev


def _normalize_match_date(value: Any) -> str:
    text = _norm_name(value)
    if not text:
        return ""
    # Keep to minute-level precision so "2026-03-07 19:35" and
    # "2026-03-07 19:35:00" map to the same natural-key.
    if len(text) >= 16:
        return text[:16]
    return text


def _natural_match_key(match: Dict[str, Any]) -> str:
    date_key = _normalize_match_date(match.get("date"))
    home = _norm_name(match.get("home_club"))
    away = _norm_name(match.get("away_club"))
    if date_key and home and away:
        return f"{date_key}|{home}|{away}"
    # Fallback only when natural key cannot be built.
    return _norm_name(match.get("match_id"))


def _merge_same_competition(data: Dict[str, Any]) -> Dict[str, Any]:
    leagues = data.get("leagues")
    if not isinstance(leagues, list) or not leagues:
        return data

    # "中职联赛" and "中超联赛" are treated as one competition.
    canonical = {"league_id": "csl", "name": "中超联赛", "standings": [], "matches": []}
    standings_by_club: Dict[str, Dict[str, Any]] = {}
    matches_by_key: Dict[str, Dict[str, Any]] = {}

    for league in leagues:
        if not isinstance(league, dict):
            continue
        for row in league.get("standings", []) or []:
            if not isinstance(row, dict):
                continue
            club_name = _norm_name(row.get("club_name"))
            if not club_name:
                continue
            standings_by_club.setdefault(club_name, row)

        for match in league.get("matches", []) or []:
            if not isinstance(match, dict):
                continue
            key = _natural_match_key(match)
            if not key:
                continue
            prev = matches_by_key.get(key)
            if prev is None:
                matches_by_key[key] = match
                continue
            matches_by_key[key] = _pick_richer_match(prev, match)

    canonical["standings"] = list(standings_by_club.values())
    canonical["matches"] = list(matches_by_key.values())
    data["leagues"] = [canonical]
    return data


def _collect_club_names(data: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    for league in data.get("leagues") or []:
        if not isinstance(league, dict):
            continue
        for row in league.get("standings") or []:
            if isinstance(row, dict):
                n = _norm_name(row.get("club_name"))
                if n:
                    names.add(n)
        for m in league.get("matches") or []:
            if not isinstance(m, dict):
                continue
            for key in ("home_club", "away_club"):
                n = _norm_name(m.get(key))
                if n:
                    names.add(n)
    return names


def _player_stats_rows_from_payload(payload: Any) -> list:
    rows = payload.get("player_stats") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _norm_name(row.get("player_name") or row.get("name") or row.get("player"))
        if name:
            out.append(row)
    return out


def load_normalized_player_stats(root: Path) -> list:
    """优先 csl_normalized.json；为空则回退到事件汇总表（CI 上 normalize 偶发空时仍可有球员名）。"""
    data_dir = root / "data"
    for fname in ("csl_normalized.json", "csl_player_stats_events_raw.json"):
        path = data_dir / fname
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        rows = _player_stats_rows_from_payload(payload)
        if rows:
            return rows
    return []


_CFL_PROFILE_KEEP_KEYS = (
    "player_id",
    "player_name",
    "player_name_en",
    "playerNameEn",
    "contestant_id",
    "contestant_club_name",
    "contestant_club_name_en",
    "contestant_name",
    "contestant_short_name",
    "contestant_name_en",
    "position",
    "position_name",
    "position_name_en",
    "position_code",
    "player_shirt_number",
    "height",
    "weight",
    "nationality",
    "nationality_en",
    "date_of_birth",
    "player_icon",
    "clubIcon",
    "contestant_icon",
    "tournament_calendar_name",
    "player_status",
    "start_date",
)


def _trim_cfl_profile_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in _CFL_PROFILE_KEEP_KEYS:
        if k not in row:
            continue
        v = row.get(k)
        if v is None or v == "":
            continue
        out[k] = v
    for ik in ("player_icon", "clubIcon", "contestant_icon"):
        u = out.get(ik)
        if isinstance(u, str) and u.startswith("//"):
            out[ik] = "https:" + u
    return out


def load_cfl_player_profiles_for_embed(root: Path) -> List[Dict[str, Any]]:
    """中足联 players/page 快照，供前端匹配展示档案与头像。"""
    path = root / "data" / "cfl_players_page_raw.json"
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    raw = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in raw:
        if isinstance(row, dict):
            trimmed = _trim_cfl_profile_row(row)
            if trimmed.get("player_name") or trimmed.get("player_id"):
                out.append(trimmed)
    return out


def _ensure_match_events_player_fields(data: Dict[str, Any]) -> None:
    """保证每条 event 同时有 player / player_name；保留 team_name / club_name（俱乐部展示）。"""
    for league in data.get("leagues") or []:
        if not isinstance(league, dict):
            continue
        for m in league.get("matches") or []:
            if not isinstance(m, dict):
                continue
            for e in m.get("events") or []:
                if not isinstance(e, dict):
                    continue
                p = resolve_event_player_name(e) or _norm_name(e.get("player") or e.get("player_name"))
                e["player"] = p
                e["player_name"] = p
                tn = _norm_name(e.get("team_name") or e.get("club_name"))
                if tn:
                    e["team_name"] = tn


def _enrich_player_stats_teams_from_match_events(data: Dict[str, Any], stats: list) -> None:
    """用各场 event.team_name 投票，为 player_stats 中空 team_name 的行补俱乐部。"""
    votes: Dict[str, Counter] = defaultdict(Counter)
    for league in data.get("leagues") or []:
        if not isinstance(league, dict):
            continue
        for m in league.get("matches") or []:
            if not isinstance(m, dict):
                continue
            for e in m.get("events") or []:
                if not isinstance(e, dict):
                    continue
                pname = _norm_name(
                    resolve_event_player_name(e) or e.get("player") or e.get("player_name")
                )
                team = _norm_name(e.get("team_name") or e.get("club_name"))
                if pname and team:
                    votes[pname][team] += 1
    for row in stats:
        if not isinstance(row, dict):
            continue
        if _norm_name(row.get("team_name")):
            continue
        pn = _norm_name(row.get("player_name"))
        if pn and votes.get(pn):
            row["team_name"] = votes[pn].most_common(1)[0][0]


def _enrich_player_stats_teams_from_ranking_files(stats: list) -> None:
    """射手/助攻榜等本地 JSON 常带 team_name；支持全名包含短名的模糊匹配。"""
    data_dir = Path(__file__).resolve().parents[2] / "data"
    ranking_rows = collect_ranking_player_rows(data_dir)
    exact = exact_team_lookup_from_rows(ranking_rows)
    for row in stats:
        if not isinstance(row, dict):
            continue
        if _norm_name(row.get("team_name")):
            continue
        pn = _norm_name(row.get("player_name"))
        if not pn:
            continue
        if pn in exact:
            row["team_name"] = exact[pn]
            continue
        fz = fuzzy_team_substring_match(pn, ranking_rows)
        if fz:
            row["team_name"] = fz


def prepare_dashboard_embed_payload(
    data: Dict[str, Any], team_logos: Dict[str, str]
) -> Tuple[Dict[str, Any], list, Dict[str, str], str]:
    """合并联赛、补全事件球员名；拆出嵌入用 lean / player_stats。"""
    work = json.loads(json.dumps(data))
    work = _merge_same_competition(work)
    _ensure_match_events_player_fields(work)
    stats = work.get("_normalized_player_stats")
    if not isinstance(stats, list):
        stats = []
    _enrich_player_stats_teams_from_match_events(work, stats)
    _enrich_player_stats_teams_from_ranking_files(stats)
    lean = {k: v for k, v in work.items() if k != "_normalized_player_stats"}
    source_file = str(work.get("_source_file") or "unknown")
    return lean, stats, team_logos, source_file


def build_dashboard_html(source_file: str, generated_at: str) -> str:
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
  <meta name="theme-color" content="#0f172a" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <title>Ultimate Football Dashboard 2026</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          fontFamily: {
            sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
            mono: ["JetBrains Mono", "ui-monospace", "monospace"]
          },
          colors: {
            midnight: "#0f172a",
            surface: "#1e293b",
            accent: "#38bdf8",
            success: "#22c55e",
            warn: "#f59e0b",
            danger: "#ef4444"
          }
        }
      }
    };
  </script>
  <style>
    html { -webkit-text-size-adjust: 100%; }
    /*
     * 积分榜宽表横向滚动：禁止单独 pan-x（会吃掉纵向滑动手势）。
     * 显式允许横纵平移，避免 iOS/WebView 把该区域当成只能横滑。
     */
    .standings-table-scroll {
      -webkit-overflow-scrolling: touch;
      overscroll-behavior-x: contain;
      touch-action: pan-x pan-y;
    }
    /* 积分榜数字列：等宽数字更易扫读 */
    .tabular-nums { font-variant-numeric: tabular-nums; }
  </style>
</head>
<body class="bg-midnight text-slate-100 min-h-screen pb-[max(4.5rem,calc(3.75rem+env(safe-area-inset-bottom,0px)))] lg:pb-0">
  <div class="mx-auto max-w-[1600px] px-2 py-2 sm:p-4 md:p-6">
    <header class="mb-2 rounded-xl border border-slate-800 bg-surface/90 p-2.5 sm:mb-3 sm:rounded-2xl sm:p-4 md:p-6">
      <div class="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div class="min-w-0">
          <p class="text-sm uppercase tracking-[0.15em] text-accent sm:tracking-[0.2em]">China Football Season Monitor 2026</p>
          <h1 class="text-xl font-black leading-tight sm:text-2xl md:text-3xl">Ultimate CSL Dashboard</h1>
          <p class="hidden text-sm text-slate-400 sm:block">Match timeline drilldown + penalty-adjusted standings + CFL player profiles</p>
        </div>
        <div class="hidden text-xs text-slate-400 rounded-md border border-slate-700 bg-slate-900/70 px-3 py-2 sm:block">
          Generated: __GENERATED__<br/>Source: __SOURCE_FILE__
        </div>
      </div>
    </header>

    <div class="grid grid-cols-1 gap-3 sm:gap-4 lg:grid-cols-[260px_1fr]">
      <aside class="hidden rounded-2xl border border-slate-800 bg-surface/90 p-4 lg:block">
        <h2 class="mb-3 text-sm font-bold uppercase tracking-wide text-slate-300">视图</h2>
        <div class="space-y-2">
          <button type="button" data-view="standings" class="view-btn view-btn-desktop w-full rounded-lg border border-accent/40 bg-accent/15 px-3 py-2 text-left text-sm font-semibold text-accent">积分榜</button>
          <button type="button" data-view="matches" class="view-btn view-btn-desktop w-full rounded-lg border border-slate-700 bg-slate-800/70 px-3 py-2 text-left text-sm font-semibold text-slate-200">赛程与事件</button>
          <button type="button" data-view="players" class="view-btn view-btn-desktop w-full rounded-lg border border-slate-700 bg-slate-800/70 px-3 py-2 text-left text-sm font-semibold text-slate-200">球员榜</button>
        </div>
      </aside>

      <main class="space-y-4">
        <section id="view-standings" class="view-panel rounded-xl border border-slate-800 bg-surface/90 p-2.5 sm:rounded-2xl sm:p-4">
          <div class="mb-2 flex flex-col gap-2 sm:mb-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
            <h2 class="text-xl font-bold leading-snug">积分榜</h2>
            <div class="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:gap-2">
              <button type="button" id="sortOfficialBtn" class="sort-standings-btn min-h-[40px] rounded-lg border border-accent/50 bg-accent/15 px-3 py-2 text-sm font-semibold leading-tight text-accent sm:min-h-0 sm:text-base">
                <span class="sm:hidden">官方积分</span><span class="hidden sm:inline">按官方积分排序</span>
              </button>
              <button type="button" id="sortMatchBtn" class="sort-standings-btn min-h-[40px] rounded-lg border border-slate-600 bg-slate-800/80 px-3 py-2 text-sm font-semibold leading-tight text-slate-200 sm:min-h-0 sm:text-base">
                <span class="sm:hidden">赛场积分</span><span class="hidden sm:inline">按赛场积分排序</span>
              </button>
            </div>
          </div>
          <details id="officialPolicyDetails" class="mb-2 rounded-lg border border-slate-700 bg-slate-900/50 text-sm text-slate-400 sm:mb-3 sm:text-base">
            <summary class="cursor-pointer select-none px-3 py-2 font-semibold text-slate-300 hover:text-accent">
              官方积分说明（中国足协 2026 赛前纪律扣分）
            </summary>
            <div id="officialPolicyBody" class="space-y-2 border-t border-slate-800 px-3 py-2 leading-relaxed"></div>
          </details>
          <div class="standings-table-scroll overflow-x-auto overscroll-x-contain -mx-1 rounded-lg px-1 sm:mx-0 sm:px-0">
            <table class="min-w-[780px] w-full text-xs sm:min-w-0 sm:text-sm md:text-base">
              <thead class="bg-slate-900/60 text-xs text-slate-300 sm:text-sm md:text-base">
                <tr>
                  <th class="sticky left-0 z-20 w-7 bg-slate-900/95 px-0.5 py-2 text-center font-semibold shadow-[2px_0_8px_rgba(0,0,0,0.3)] sm:w-auto sm:px-2">#</th>
                  <th class="sticky left-7 z-20 min-w-[10rem] w-[10rem] max-w-[11rem] bg-slate-900/95 px-1 py-2 text-left font-semibold shadow-[2px_0_8px_rgba(0,0,0,0.3)] sm:left-8 sm:min-w-0 sm:w-auto sm:max-w-none sm:px-2">球队</th>
                  <th class="px-0.5 py-2 text-center font-medium tabular-nums sm:px-2">赛</th>
                  <th class="px-0.5 py-2 text-center font-medium sm:px-2"><span class="sm:hidden">胜平负</span><span class="hidden sm:inline">胜-平-负</span></th>
                  <th class="hidden px-0.5 py-2 text-center font-medium sm:table-cell sm:px-2">近况</th>
                  <th class="px-0.5 py-2 text-center font-medium tabular-nums sm:px-2">进</th>
                  <th class="px-0.5 py-2 text-center font-medium tabular-nums sm:px-2">失</th>
                  <th class="px-0.5 py-2 text-center font-medium tabular-nums sm:px-2">净</th>
                  <th class="px-0.5 py-2 text-center font-medium tabular-nums sm:px-2" title="仅由已赛场次胜平负累计">赛场</th>
                  <th class="px-0.5 py-2 text-center font-medium tabular-nums sm:px-2" title="赛季开赛前足协纪律处罚">扣</th>
                  <th class="px-0.5 py-2 text-center font-semibold tabular-nums sm:px-2" title="赛场积分 − 赛前扣分">官方</th>
                </tr>
              </thead>
              <tbody id="standingsBody"></tbody>
            </table>
          </div>
        </section>

        <section id="view-matches" class="view-panel hidden">
          <div class="rounded-xl border border-slate-800 bg-surface/90 p-2.5 sm:rounded-2xl sm:p-4">
            <div class="mb-2 flex flex-col gap-2 sm:mb-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
              <h2 class="text-lg font-bold sm:text-xl">赛程与事件</h2>
            </div>
            <div class="mb-2 flex flex-col gap-1.5 sm:mb-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-2">
              <label for="matchClubFilter" class="text-sm font-medium text-slate-400 sm:shrink-0">俱乐部筛选</label>
              <select id="matchClubFilter" class="min-h-[34px] w-full max-w-lg rounded-lg border border-slate-600 bg-slate-800/90 px-2.5 py-1.5 text-sm text-slate-100 shadow-inner sm:min-h-[40px] sm:min-w-[220px] sm:px-3 sm:py-2 sm:text-base">
                <option value="">全部俱乐部</option>
              </select>
              <button id="clearClubFilterBtn" type="button" class="hidden min-h-[34px] rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs hover:border-accent/40 sm:min-h-0 sm:px-3 sm:py-1 sm:text-sm">
                清除筛选
              </button>
            </div>
            <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:gap-5">
              <!-- 源码顺序：月历在上（移动端首屏）；lg:order 让桌面仍为左列表右月历 -->
              <div id="matchCalendarPanel" class="shrink-0 rounded-xl border border-slate-700 bg-slate-900/45 p-2 sm:p-2.5 lg:order-2 lg:w-[min(100%,320px)]">
                <div class="mb-1.5 flex items-center justify-between gap-1.5">
                  <button type="button" id="matchCalPrev" class="min-h-[36px] min-w-[36px] rounded-md border border-slate-600 bg-slate-800/90 text-base leading-none text-slate-200 hover:border-accent/40 hover:text-accent sm:min-h-0 sm:min-w-0 sm:rounded-lg sm:px-2 sm:py-1 sm:text-lg" aria-label="上一段">‹</button>
                  <div id="matchCalTitle" class="min-w-0 flex-1 text-center text-xs font-semibold leading-tight text-slate-100 sm:text-sm"></div>
                  <button type="button" id="matchCalNext" class="min-h-[36px] min-w-[36px] rounded-md border border-slate-600 bg-slate-800/90 text-base leading-none text-slate-200 hover:border-accent/40 hover:text-accent sm:min-h-0 sm:min-w-0 sm:rounded-lg sm:px-2 sm:py-1 sm:text-lg" aria-label="下一段">›</button>
                  <select id="matchCalRange" class="min-h-[30px] rounded-md border border-slate-600 bg-slate-800/90 px-2 py-1 text-xs text-slate-100 sm:text-sm">
                    <option value="week">1周</option>
                    <option value="month">1个月</option>
                  </select>
                </div>
                <div class="grid grid-cols-7 gap-0.5 text-center text-xs font-medium uppercase tracking-wide text-slate-500 sm:text-sm">
                  <span>日</span><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span>
                </div>
                <div id="matchCalCells" class="mt-0.5 grid grid-cols-7 gap-0.5 sm:gap-1"></div>
                <button type="button" id="matchCalResetDay" class="mt-2 hidden w-full rounded-lg border border-slate-600 bg-slate-800/80 py-2 text-sm font-medium text-slate-200 hover:border-accent/40 hover:text-accent">
                  显示当前范围全部场次
                </button>
              </div>
              <div id="matchListWrap" class="min-w-0 lg:order-1 lg:min-h-0 lg:flex-1">
                <div id="matchList" class="max-h-[min(42dvh,calc(100dvh-18rem))] overflow-auto pr-0.5 sm:max-h-[min(720px,70vh)] sm:pr-1"></div>
              </div>
            </div>
          </div>
        </section>

        <section id="view-players" class="view-panel hidden space-y-2 sm:space-y-4">
          <div id="playersSubviewList" class="space-y-2 sm:space-y-4">
            <div class="rounded-xl border border-slate-800 bg-surface/90 p-2.5 sm:rounded-2xl sm:p-4">
              <h2 class="mb-1 text-xl font-bold leading-snug sm:mb-2">球员榜</h2>
              <p class="mb-2 text-sm leading-snug text-slate-500 sm:mb-3 sm:text-base">筛选俱乐部后点球员名查看数据与注册档案</p>
              <div class="mb-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
                <label for="playerClubFilter" class="text-sm font-medium text-slate-400 sm:shrink-0">俱乐部筛选</label>
                <select id="playerClubFilter" class="min-h-[40px] w-full max-w-lg rounded-lg border border-slate-600 bg-slate-800/90 px-3 py-2 text-base text-slate-100 shadow-inner sm:min-w-[240px]">
                  <option value="">全部俱乐部</option>
                </select>
              </div>
              <div id="playerList" class="max-h-[min(58dvh,calc(100dvh-13rem))] overflow-auto pr-0.5 sm:max-h-[min(720px,75vh)] sm:pr-1"></div>
            </div>
          </div>
          <div id="playersSubviewProfile" class="hidden">
            <div class="rounded-xl border border-slate-800 bg-surface/90 p-2.5 sm:rounded-2xl sm:p-4">
              <div class="mb-3 flex flex-wrap items-center gap-2">
                <button type="button" id="backToPlayerListBtn" class="min-h-[40px] rounded-lg border border-slate-600 bg-slate-800/80 px-3 py-2 text-sm font-semibold text-slate-200 hover:border-accent/40 hover:text-accent sm:min-h-0">
                  ← 返回球员榜
                </button>
              </div>
              <h2 class="mb-2 text-lg font-bold sm:text-xl">球员档案</h2>
              <div id="playerProfileTitle" class="mb-3 text-sm text-slate-400 sm:text-base"></div>
              <div id="playerProfileBody" class="max-h-[min(70dvh,560px)] overflow-y-auto pr-1 text-base"></div>
            </div>
          </div>
        </section>
      </main>
    </div>
  </div>

  <nav class="fixed bottom-0 left-0 right-0 z-40 border-t border-slate-800 bg-slate-900/98 backdrop-blur-md shadow-[0_-4px_20px_rgba(0,0,0,0.35)] lg:hidden" style="padding-bottom: env(safe-area-inset-bottom, 0px);">
    <div class="mx-auto flex max-w-[1600px]">
      <button type="button" data-view="standings" class="view-btn flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 border-t-2 border-transparent px-1 py-2 text-sm font-semibold tracking-tight text-slate-200 active:bg-slate-800/80">
        <span class="text-lg leading-none">📊</span>
        <span>积分榜</span>
      </button>
      <button type="button" data-view="matches" class="view-btn flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 border-t-2 border-transparent px-1 py-2 text-sm font-semibold tracking-tight text-slate-200 active:bg-slate-800/80">
        <span class="text-lg leading-none">⚽</span>
        <span>赛程</span>
      </button>
      <button type="button" data-view="players" class="view-btn flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 border-t-2 border-transparent px-1 py-2 text-sm font-semibold tracking-tight text-slate-200 active:bg-slate-800/80">
        <span class="text-lg leading-none">📈</span>
        <span>球员</span>
      </button>
    </div>
  </nav>

  <div id="matchModal" class="fixed inset-0 z-50 hidden">
    <div id="matchModalOverlay" class="absolute inset-0 bg-black/60 backdrop-blur-[2px]"></div>
    <div class="absolute inset-x-2 bottom-2 flex h-[min(88dvh,calc(100dvh-env(safe-area-inset-bottom,0px)-1rem))] flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl sm:inset-x-auto sm:bottom-0 sm:left-auto sm:right-0 sm:top-0 sm:h-full sm:max-h-none sm:w-full sm:max-w-2xl sm:rounded-none sm:border-l sm:border-t-0">
      <div class="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 px-3 py-3 sm:px-4">
        <div class="min-w-0 flex-1 pr-1">
          <h3 class="text-base font-bold sm:text-lg">比赛事件</h3>
          <div id="modalTitle" class="mt-1 min-w-0 text-sm sm:text-base"></div>
        </div>
        <button type="button" id="closeModalBtn" class="min-h-[44px] shrink-0 rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-base hover:border-accent/40 sm:min-h-0 sm:py-1">关闭</button>
      </div>
      <div id="modalTimeline" class="min-h-0 flex-1 overflow-y-auto overscroll-y-contain p-3 space-y-3 sm:p-4"></div>
    </div>
  </div>

  <script>
    /** 数据从同目录 dashboard_embed.json 加载，避免内联巨型 JSON 被 HTML/浏览器截断或误解析 */
    var RAW_DATA, RAW_PLAYER_STATS, TEAM_LOGOS, RAW_CFL_PROFILES;

    const standingsBodyEl = document.getElementById("standingsBody");
    const matchListEl = document.getElementById("matchList");
    const matchModal = document.getElementById("matchModal");
    const matchModalOverlay = document.getElementById("matchModalOverlay");
    const closeModalBtn = document.getElementById("closeModalBtn");
    const modalTitleEl = document.getElementById("modalTitle");
    const modalTimelineEl = document.getElementById("modalTimeline");
    const clearClubFilterBtn = document.getElementById("clearClubFilterBtn");
    const matchClubFilterEl = document.getElementById("matchClubFilter");
    const playerListEl = document.getElementById("playerList");
    const playerClubFilterEl = document.getElementById("playerClubFilter");
    const playerProfileTitleEl = document.getElementById("playerProfileTitle");
    const playerProfileBodyEl = document.getElementById("playerProfileBody");
    const playersSubviewListEl = document.getElementById("playersSubviewList");
    const playersSubviewProfileEl = document.getElementById("playersSubviewProfile");
    const backToPlayerListBtn = document.getElementById("backToPlayerListBtn");
    const viewButtons = document.querySelectorAll(".view-btn");
    const viewPanels = document.querySelectorAll(".view-panel");
    const sortOfficialBtn = document.getElementById("sortOfficialBtn");
    const sortMatchBtn = document.getElementById("sortMatchBtn");
    const officialPolicyBodyEl = document.getElementById("officialPolicyBody");
    const officialPolicyDetailsEl = document.getElementById("officialPolicyDetails");

    let currentView = "standings";
    let selectedClub = "";
    let selectedPlayerClub = "";
    let standingsSortMode = "official";
    /** 赛程日历窗口起始（默认 1 周，周日 YYYY-MM-DD） */
    let calendarWindowStartYmd = "";
    /** week=1周（默认）；month=1个月 */
    let calendarRangeMode = "week";
    let selectedMatchDate = "";
    /** 俱乐部/数据源切换后需重新按「最近赛日」锚定 */
    let matchViewNeedsDateAnchor = true;
    let lastScheduleAnchorClubSig = "__unset__";
    /** month=按月列表；global_adjacent=全体时前一有赛日+后一有赛日；club_all=当前俱乐部全部已赛+未赛 */
    let scheduleViewMode = "month";
    /** 俱乐部锚定：在日历上圈选「最近一场已赛」日期（非单日筛选） */
    let calendarHighlightYmd = "";
    /** 俱乐部锚定：列表滚动到该日期分组（渲染后消费） */
    let matchScrollAnchorYmd = "";

    const matchCalTitleEl = document.getElementById("matchCalTitle");
    const matchCalCellsEl = document.getElementById("matchCalCells");
    const matchCalPrevBtn = document.getElementById("matchCalPrev");
    const matchCalNextBtn = document.getElementById("matchCalNext");
    const matchCalRangeEl = document.getElementById("matchCalRange");
    const matchCalResetDayBtn = document.getElementById("matchCalResetDay");

    function safeArr(v){ return Array.isArray(v) ? v : []; }
    function n(v){ const x = Number(v); return Number.isFinite(x) ? x : 0; }
    function esc(s){ return String(s ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;"); }

    function optionValueAttr(s){
      return String(s ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
    }

    function eventPlayerName(e){
      if (!e || typeof e !== "object") return "";
      const raw = e.player ?? e.player_name ?? e.eventPlayerName ?? e.event_player_name ?? e.name ?? e.playerName ?? e.athleteName ?? e.athlete_name ?? e.label ?? "";
      return String(raw).trim();
    }

    function clubImg(name, compact){
      const src = TEAM_LOGOS[name] || "";
      const initial = String(name || "").trim().slice(0, 1) || "?";
      const box = compact
        ? "inline-flex h-5 w-5 shrink-0 items-center justify-center"
        : "inline-flex h-7 w-7 shrink-0 items-center justify-center sm:h-8 sm:w-8";
      if (!src){
        return `<span class="${box} rounded border border-slate-700 bg-slate-900 text-xs font-bold text-slate-500 sm:rounded-md sm:text-sm" title="${esc(name)}">${esc(initial)}</span>`;
      }
      return `<span class="${box} overflow-hidden rounded border border-slate-700 bg-slate-900 sm:rounded-md">
        <img src="${esc(src)}" alt="" class="h-full w-full object-contain p-px sm:p-0.5" loading="lazy" onerror="this.style.display='none'; const f=this.nextElementSibling; if(f) f.classList.remove('hidden');" />
        <span class="hidden h-full w-full items-center justify-center text-xs font-bold text-slate-500 sm:text-sm">${esc(initial)}</span>
      </span>`;
    }

    function resultEmoji(r){
      if (r === "W") return "🟢";
      if (r === "D") return "🟡";
      return "🔴";
    }

    function getLeague(){
      return safeArr(RAW_DATA.leagues)[0] || {matches:[], standings:[]};
    }

    function computeForm(clubName, matches){
      const finished = safeArr(matches).filter(m => String(m.status).toLowerCase() === "finished");
      const related = finished.filter(m => m.home_club === clubName || m.away_club === clubName);
      related.sort((a,b) => String(b.date||"").localeCompare(String(a.date||"")));
      return related.slice(0,5).map(m => {
        const hs = n(m?.score?.home), as = n(m?.score?.away);
        const home = m.home_club === clubName;
        if (hs === as) return "D";
        const won = home ? hs > as : as > hs;
        return won ? "W" : "L";
      });
    }

    /**
     * 基于比赛结果实时重算积分榜：
     * - 每次数据刷新后按 matches 重算已赛场次/积分/净胜球，避免沿用滞后的 standings。
     * - 扣分优先采用官方 policy，同时兼容 standings 行内 penalty_points。
     */
    function buildLiveStandingsRows(league, matches){
      const rows = safeArr(league && league.standings);
      const baseByClub = new Map();
      const clubOrder = [];
      const seenClub = new Set();

      for (const row of rows){
        if (!row || typeof row !== "object") continue;
        const club = String(row.club_name || "").trim();
        if (!club) continue;
        baseByClub.set(club, row);
        if (!seenClub.has(club)){
          seenClub.add(club);
          clubOrder.push(club);
        }
      }
      for (const m of safeArr(matches)){
        const h = String(m?.home_club || "").trim();
        const a = String(m?.away_club || "").trim();
        if (h && !seenClub.has(h)){ seenClub.add(h); clubOrder.push(h); }
        if (a && !seenClub.has(a)){ seenClub.add(a); clubOrder.push(a); }
      }

      const penaltyByClub = Object.create(null);
      const policy = RAW_DATA && RAW_DATA.official_points_policy;
      const deductions = policy && typeof policy === "object" ? policy.deductions_by_club : null;
      if (deductions && typeof deductions === "object" && !Array.isArray(deductions)){
        for (const [club, val] of Object.entries(deductions)){
          penaltyByClub[String(club)] = n(val);
        }
      }
      for (const row of rows){
        const club = String(row?.club_name || "").trim();
        if (!club) continue;
        const p = n(row?.penalty_points);
        if (!(club in penaltyByClub)) penaltyByClub[club] = p;
        else penaltyByClub[club] = Math.max(n(penaltyByClub[club]), p);
      }

      const statsByClub = new Map();
      const ensureStats = (club) => {
        if (!statsByClub.has(club)){
          statsByClub.set(club, { played: 0, w: 0, d: 0, l: 0, gf: 0, ga: 0, points: 0 });
        }
        return statsByClub.get(club);
      };

      const seenMatches = new Set();
      for (const m of safeArr(matches)){
        const key = String(m?.match_id || "").trim() || `${String(m?.date || "").trim()}|${String(m?.home_club || "").trim()}|${String(m?.away_club || "").trim()}`;
        if (seenMatches.has(key)) continue;
        seenMatches.add(key);

        if (!isFinishedMatch(m)) continue;
        const homeClub = String(m?.home_club || "").trim();
        const awayClub = String(m?.away_club || "").trim();
        if (!homeClub || !awayClub) continue;
        const hs = Number(m?.score?.home);
        const as = Number(m?.score?.away);
        if (!Number.isFinite(hs) || !Number.isFinite(as)) continue;

        const home = ensureStats(homeClub);
        const away = ensureStats(awayClub);
        home.played += 1; away.played += 1;
        home.gf += hs; home.ga += as;
        away.gf += as; away.ga += hs;

        if (hs > as){
          home.w += 1; away.l += 1;
          home.points += 3;
        } else if (hs < as){
          away.w += 1; home.l += 1;
          away.points += 3;
        } else {
          home.d += 1; away.d += 1;
          home.points += 1; away.points += 1;
        }
      }

      const out = [];
      for (const club of clubOrder){
        const base = baseByClub.get(club) || {};
        const s = ensureStats(club);
        const penalty = n(penaltyByClub[club] || 0);
        out.push({
          ...base,
          club_name: club,
          played: s.played,
          w_d_l: [s.w, s.d, s.l],
          summary: { ...(base.summary && typeof base.summary === "object" ? base.summary : {}), goals_for: s.gf, goals_against: s.ga },
          goal_difference: s.gf - s.ga,
          points: s.points,
          penalty_points: penalty,
          effective_points: s.points - penalty,
        });
      }
      return out;
    }

    function renderLeagueNav(){
      /* 头部已不展示联赛名称条 */
    }

    function renderOfficialPolicy(){
      const pol = RAW_DATA.official_points_policy;
      if (!pol || !officialPolicyBodyEl){
        if (officialPolicyDetailsEl) officialPolicyDetailsEl.classList.add("hidden");
        return;
      }
      if (officialPolicyDetailsEl) officialPolicyDetailsEl.classList.remove("hidden");
      const lines = [];
      if (pol.policy_title) lines.push(`<p class="font-medium text-slate-300">${esc(pol.policy_title)}</p>`);
      if (pol.announcement_date) lines.push(`<p>公告日期：${esc(pol.announcement_date)}</p>`);
      if (pol.summary) lines.push(`<p>${esc(pol.summary)}</p>`);
      const dbc = pol.deductions_by_club;
      if (dbc && typeof dbc === "object" && !Array.isArray(dbc)){
        const names = Object.keys(dbc).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
        if (names.length){
          lines.push('<p class="font-medium text-slate-300">赛季前已生效扣分（与积分榜「赛前扣」列一致）</p>');
          lines.push('<div class="overflow-x-auto"><table class="w-full min-w-[240px] border-collapse text-left text-sm sm:text-base">');
          lines.push('<thead><tr class="border-b border-slate-700 text-slate-500"><th class="py-1.5 pr-3 font-medium">俱乐部</th><th class="py-1.5 font-medium">扣分</th></tr></thead><tbody>');
          for (const club of names){
            lines.push(`<tr class="border-b border-slate-800/80"><td class="py-1.5 pr-3 text-slate-200">${esc(club)}</td><td class="py-1.5 font-mono text-danger">-${esc(String(dbc[club]))}</td></tr>`);
          }
          lines.push("</tbody></table></div>");
        }
      }
      const refs = safeArr(pol.references);
      if (refs.length){
        lines.push('<p class="font-medium text-slate-300">参考链接：</p><ul class="list-inside list-disc space-y-1">');
        for (const r of refs){
          const t = esc(r.title || r.url || "");
          const u = esc(r.url || "");
          if (u) lines.push(`<li><a class="text-accent underline" href="${u}" target="_blank" rel="noopener noreferrer">${t}</a></li>`);
        }
        lines.push("</ul>");
      }
      officialPolicyBodyEl.innerHTML = lines.join("");
    }

    function sortStandingsRows(rows, mode){
      const out = rows.slice();
      const tieBreak = (a, b) => {
        const gd = n(b.goal_difference) - n(a.goal_difference);
        if (gd !== 0) return gd;
        const gf = n(b?.summary?.goals_for) - n(a?.summary?.goals_for);
        if (gf !== 0) return gf;
        return String(a.club_name || "").localeCompare(String(b.club_name || ""));
      };
      if (mode === "match"){
        out.sort((a, b) => {
          const p = n(b.points) - n(a.points);
          if (p !== 0) return p;
          return tieBreak(a, b);
        });
      } else {
        out.sort((a, b) => {
          const p = n(b.effective_points) - n(a.effective_points);
          if (p !== 0) return p;
          return tieBreak(a, b);
        });
      }
      return out;
    }

    function updateSortButtons(){
      const onO = standingsSortMode === "official";
      if (sortOfficialBtn){
        sortOfficialBtn.classList.toggle("border-accent/50", onO);
        sortOfficialBtn.classList.toggle("bg-accent/15", onO);
        sortOfficialBtn.classList.toggle("text-accent", onO);
        sortOfficialBtn.classList.toggle("border-slate-600", !onO);
        sortOfficialBtn.classList.toggle("bg-slate-800/80", !onO);
        sortOfficialBtn.classList.toggle("text-slate-200", !onO);
      }
      if (sortMatchBtn){
        const onM = standingsSortMode === "match";
        sortMatchBtn.classList.toggle("border-accent/50", onM);
        sortMatchBtn.classList.toggle("bg-accent/15", onM);
        sortMatchBtn.classList.toggle("text-accent", onM);
        sortMatchBtn.classList.toggle("border-slate-600", !onM);
        sortMatchBtn.classList.toggle("bg-slate-800/80", !onM);
        sortMatchBtn.classList.toggle("text-slate-200", !onM);
      }
    }

    function renderStandings(){
      const league = getLeague();
      const matches = safeArr(league.matches);
      const standings = sortStandingsRows(buildLiveStandingsRows(league, matches), standingsSortMode);
      updateSortButtons();
      standingsBodyEl.innerHTML = standings.map((row, idx) => {
        const wdl = safeArr(row.w_d_l);
        const form = computeForm(row.club_name, matches);
        return `
          <tr class="border-b border-slate-800 hover:bg-slate-800/50">
            <td class="sticky left-0 z-10 w-7 bg-slate-900/95 px-0.5 py-1.5 text-center text-xs font-medium tabular-nums shadow-[2px_0_8px_rgba(0,0,0,0.2)] sm:w-auto sm:px-2 sm:py-2 sm:text-sm md:text-base">${idx + 1}</td>
            <td class="sticky left-7 z-10 min-w-[10rem] w-[10rem] max-w-[11rem] bg-slate-900/95 px-1 py-1.5 sm:left-8 sm:min-w-0 sm:w-auto sm:max-w-none sm:px-2 sm:py-2">
              <div class="flex min-w-0 items-center gap-0.5 sm:gap-2">
                ${clubImg(row.club_name, true)}
                <button type="button" data-club-name="${esc(row.club_name)}" class="club-filter-btn min-h-[36px] min-w-0 flex-1 truncate rounded px-0 py-0.5 text-left text-xs leading-tight text-accent hover:bg-accent/10 sm:min-h-0 sm:px-1 sm:text-base sm:leading-normal hover:underline" title="${esc(row.club_name)}">
                  ${esc(row.club_name)}
                </button>
              </div>
            </td>
            <td class="px-0.5 py-1.5 text-center text-xs tabular-nums text-slate-200 sm:px-2 sm:py-2 sm:text-sm md:text-base">${n(row.played)}</td>
            <td class="px-0.5 py-1.5 text-center text-xs sm:px-2 sm:py-2 sm:text-sm md:text-base">
              <span class="font-mono text-xs tabular-nums leading-none text-slate-300 sm:hidden" title="胜-平-负">${n(wdl[0])}-${n(wdl[1])}-${n(wdl[2])}</span>
              <span class="hidden sm:inline-flex sm:flex-wrap sm:items-center sm:gap-0.5">
                <span class="inline-flex items-center gap-0.5 rounded bg-success/20 px-1 py-0.5 text-xs text-success sm:gap-1 sm:px-2 sm:text-sm">胜${n(wdl[0])}</span>
                <span class="inline-flex items-center gap-0.5 rounded bg-warn/20 px-1 py-0.5 text-xs text-warn sm:gap-1 sm:px-2 sm:text-sm">平${n(wdl[1])}</span>
                <span class="inline-flex items-center gap-0.5 rounded bg-danger/20 px-1 py-0.5 text-xs text-danger sm:gap-1 sm:px-2 sm:text-sm">负${n(wdl[2])}</span>
              </span>
            </td>
            <td class="hidden whitespace-nowrap px-0.5 py-1.5 text-center text-xs sm:table-cell sm:px-2 sm:py-2 sm:text-sm md:text-base">${form.map(resultEmoji).join(" ") || "—"}</td>
            <td class="px-0.5 py-1.5 text-center text-xs tabular-nums text-slate-200 sm:px-2 sm:py-2 sm:text-sm md:text-base">${n(row?.summary?.goals_for)}</td>
            <td class="px-0.5 py-1.5 text-center text-xs tabular-nums text-slate-200 sm:px-2 sm:py-2 sm:text-sm md:text-base">${n(row?.summary?.goals_against)}</td>
            <td class="px-0.5 py-1.5 text-center text-xs tabular-nums sm:px-2 sm:py-2 sm:text-sm md:text-base ${n(row.goal_difference) >= 0 ? "text-success" : "text-danger"}">${n(row.goal_difference)}</td>
            <td class="px-0.5 py-1.5 text-center text-xs font-semibold tabular-nums text-slate-200 sm:px-2 sm:py-2 sm:text-sm md:text-base">${n(row.points)}</td>
            <td class="px-0.5 py-1.5 text-center text-xs tabular-nums sm:px-2 sm:py-2 sm:text-sm md:text-base ${n(row.penalty_points) > 0 ? "text-danger font-semibold" : "text-slate-300"}">-${n(row.penalty_points)}</td>
            <td class="px-0.5 py-1.5 text-center text-xs font-bold tabular-nums text-accent sm:px-2 sm:py-2 sm:text-sm md:text-base">${n(row.effective_points)}</td>
          </tr>
        `;
      }).join("");

      document.querySelectorAll(".club-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          selectedClub = btn.dataset.clubName || "";
          renderMatches();
          switchView("matches");
        });
      });
    }

    function fmtScore(m){
      const hs = m?.score?.home;
      const as = m?.score?.away;
      if (hs === null || hs === undefined || as === null || as === undefined) return ":";
      return `${hs}:${as}`;
    }

    function venueStadium(m){
      const v = m?.venue && typeof m.venue === "object" ? m.venue.name : "";
      const s = String(v || "").trim();
      return s;
    }

    function openMatchModal(match){
      const home = match.home_club || "";
      const away = match.away_club || "";
      const stadium = venueStadium(match);
      const stadiumLine = stadium ? `体育场：${esc(stadium)}` : "体育场：待公布";
      const hf = String(match.home_formation_used || "").trim();
      const af = String(match.away_formation_used || "").trim();
      const formLine = (hf || af)
        ? `<div class="mb-3 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-300 sm:text-base"><span class="text-slate-500">阵型</span> 主 ${esc(hf || "—")} · 客 ${esc(af || "—")}</div>`
        : "";
      modalTitleEl.innerHTML = `
        <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-slate-300 sm:gap-x-2">
          ${clubImg(home)}
          <span class="max-w-[min(42vw,9rem)] truncate text-sm font-semibold sm:max-w-none sm:text-base" title="${esc(home)}">${esc(home)}</span>
          <span class="rounded bg-slate-800 px-2 py-1 font-mono text-base font-bold text-accent sm:py-0.5">${esc(fmtScore(match))}</span>
          ${clubImg(away)}
          <span class="max-w-[min(42vw,9rem)] truncate text-sm font-semibold sm:max-w-none sm:text-base" title="${esc(away)}">${esc(away)}</span>
          <span class="w-full shrink-0 text-sm text-slate-500 sm:ml-1 sm:w-auto sm:text-base">· ${esc(match.date || "")}</span>
          <span class="w-full text-sm leading-snug text-slate-500 sm:text-base">${stadiumLine}</span>
        </div>
      `;
      const events = safeArr(match.events).slice().sort((a,b)=>n(a.minute)-n(b.minute));
      let body = formLine;
      if (!events.length){
        body += '<div class="text-base text-slate-400">本场比赛暂无事件数据（未赛或数据源未更新）。</div>';
      } else {
        body += events.map(e => `
          <div class="relative pl-6 sm:pl-7">
            <span class="absolute left-0 top-2 h-2 w-2 rounded-full bg-accent"></span>
            <div class="rounded-lg border border-slate-700 bg-slate-800/70 p-2.5 sm:p-3">
              <div class="mb-1 flex items-center justify-between gap-2">
                <span class="rounded px-2 py-0.5 text-sm sm:text-sm ${eventBadge(e.type)}">${esc(e.type)}</span>
                <span class="font-mono text-sm text-slate-300 sm:text-base">${esc(e.minute)}'</span>
              </div>
              <div class="text-sm font-semibold sm:text-base">${esc(eventPlayerName(e) || "未知球员")}</div>
            </div>
          </div>
        `).join("");
      }
      modalTimelineEl.innerHTML = body;
      matchModal.classList.remove("hidden");
      document.body.style.overflow = "hidden";
    }

    function closeMatchModal(){
      matchModal.classList.add("hidden");
      document.body.style.overflow = "";
    }

    function matchDateKey(m){
      const raw = String(m?.date || "").trim();
      if (raw.length >= 10) return raw.slice(0, 10);
      return "";
    }

    function pad2(n){ return String(n).padStart(2, "0"); }

    function todayYmd(){
      const t = new Date();
      return `${t.getFullYear()}-${pad2(t.getMonth() + 1)}-${pad2(t.getDate())}`;
    }

    function parseLocalYmd(ymd){
      const p = String(ymd || "").slice(0, 10);
      if (p.length !== 10 || p[4] !== "-" || p[7] !== "-") return null;
      const y = Number(p.slice(0, 4)), mo = Number(p.slice(5, 7)) - 1, d = Number(p.slice(8, 10));
      const dt = new Date(y, mo, d);
      if (dt.getFullYear() !== y || dt.getMonth() !== mo || dt.getDate() !== d) return null;
      return dt;
    }

    function fmtLocalYmd(dt){
      return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}`;
    }

    function addDaysYmd(ymd, delta){
      const dt = parseLocalYmd(ymd);
      if (!dt) return String(ymd || "").slice(0, 10);
      dt.setDate(dt.getDate() + delta);
      return fmtLocalYmd(dt);
    }

    /** 该日所在周的起始周日 YYYY-MM-DD（表头为日一二…六） */
    function sundayWeekContainingYmd(ymd){
      const dt = parseLocalYmd(ymd) || parseLocalYmd(todayYmd());
      if (!dt) return todayYmd();
      const dow = dt.getDay();
      dt.setDate(dt.getDate() - dow);
      return fmtLocalYmd(dt);
    }

    function monthPrefixYmFromYmd(ymd){
      return String(ymd || "").slice(0, 7);
    }

    function shiftMonthYmd(ymd, deltaMonths){
      const dt = parseLocalYmd(ymd) || parseLocalYmd(todayYmd());
      if (!dt) return todayYmd();
      const d = dt.getDate();
      dt.setDate(1);
      dt.setMonth(dt.getMonth() + deltaMonths);
      const lastDay = new Date(dt.getFullYear(), dt.getMonth() + 1, 0).getDate();
      dt.setDate(Math.min(d, lastDay));
      return fmtLocalYmd(dt);
    }

    function matchInCalendarRange(m, windowStartYmd, mode){
      const k = matchDateKey(m);
      if (k.length !== 10 || !windowStartYmd) return false;
      if (mode === "month"){
        return k.slice(0, 7) === monthPrefixYmFromYmd(windowStartYmd);
      }
      const endY = addDaysYmd(windowStartYmd, 6);
      return k >= windowStartYmd && k <= endY;
    }

    function formatRangeTitle(startYmd, mode){
      if (mode === "month"){
        const dt = parseLocalYmd(startYmd);
        if (!dt) return "";
        return `${dt.getFullYear()}年${dt.getMonth() + 1}月`;
      }
      const endYmd = addDaysYmd(startYmd, 6);
      const s = parseLocalYmd(startYmd);
      const e = parseLocalYmd(endYmd);
      if (!s || !e) return "";
      const sy = s.getFullYear(), sm = s.getMonth() + 1, sd = s.getDate();
      const ey = e.getFullYear(), em = e.getMonth() + 1, ed = e.getDate();
      if (sy === ey) return `${sy}年${sm}月${sd}日–${em}月${ed}日`;
      return `${sy}年${sm}月${sd}日–${ey}年${em}月${ed}日`;
    }

    /** 优先今日有赛，否则最近未来赛日，否则已赛最后一日（均相对系统日期） */
    function nearestMatchDateYmd(matches){
      const dates = [...new Set(matches.map(matchDateKey).filter(k => k.length === 10))].sort();
      if (!dates.length) return "";
      const today = todayYmd();
      if (dates.includes(today)) return today;
      const next = dates.find(d => d >= today);
      if (next) return next;
      return dates[dates.length - 1];
    }

    function matchStatusNorm(m){
      return String(m && m.status != null ? m.status : "").toLowerCase();
    }

    function isFinishedMatch(m){
      const s = matchStatusNorm(m);
      return s === "finished" || s === "completed" || s === "ft";
    }

    function isLiveMatch(m){
      const s = matchStatusNorm(m);
      return s === "live" || s === "in_progress" || s === "playing";
    }

    function isUpcomingMatch(m){
      if (isFinishedMatch(m) || isLiveMatch(m)) return false;
      const s = matchStatusNorm(m);
      if (s === "cancelled" || s === "canceled" || s === "postponed") return false;
      return true;
    }

    /**
     * 全体俱乐部、首次进入：相对**系统当日**（日历），不按「nearest 在数组里的左右邻」取日（否则会跳过紧邻的下一有赛日）。
     * 上一有赛日 = 严格早于今日的最晚一天；下一有赛日 = 严格晚于今日的最早一天（故今日 13 号、有赛 12/17/18 → 12 与 17，而非 12 与 18）。
     */
    function buildAdjacentMatchDaysWindow(matches){
      const days = [...new Set(matches.map(matchDateKey).filter(k => k.length === 10))].sort();
      if (!days.length) return [];
      const t = todayYmd();
      const before = days.filter(d => d < t);
      const after = days.filter(d => d > t);
      let prevD = before.length ? before[before.length - 1] : "";
      let nextD = after.length ? after[0] : "";
      const want = new Set();
      if (prevD) want.add(prevD);
      if (nextD) want.add(nextD);
      if (want.size === 0 && days.length){
        want.add(days[0]);
        if (days.length > 1) want.add(days[days.length - 1]);
      } else if (want.size === 1){
        const only = [...want][0];
        const ix = days.indexOf(only);
        if (ix > 0) want.add(days[ix - 1]);
        else if (ix < days.length - 1) want.add(days[ix + 1]);
      }
      return matches.filter(m => want.has(matchDateKey(m)))
        .sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
    }

    /** 相对系统日期：最近一场已完成的赛日（≤今天中取最晚；若无则取已赛场中最晚一天） */
    function lastCompletedDateYmd(matches){
      const today = todayYmd();
      const fins = matches.filter(isFinishedMatch).map(m => matchDateKey(m)).filter(k => k.length === 10);
      if (!fins.length) return "";
      fins.sort((a, b) => a.localeCompare(b));
      const past = fins.filter(d => d <= today);
      if (past.length) return past[past.length - 1];
      return fins[fins.length - 1];
    }

    function firstUpcomingDateYmd(matches){
      const sorted = matches.slice().sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
      for (const m of sorted){
        if (isUpcomingMatch(m) || isLiveMatch(m)) return matchDateKey(m);
      }
      return "";
    }

    function matchStatusLabel(status){
      const s = String(status || "").toLowerCase();
      if (s === "finished") return "已结束";
      if (s === "live" || s === "in_progress" || s === "playing") return "进行中";
      if (s === "postponed") return "延期";
      if (s === "cancelled" || s === "canceled") return "取消";
      return "未开始";
    }

    function matchStatusUi(status){
      const s = String(status || "").toLowerCase();
      let cls = "bg-warn/20 text-warn";
      if (s === "finished") cls = "bg-success/20 text-success";
      else if (s === "live" || s === "in_progress" || s === "playing") cls = "bg-danger/25 text-red-200";
      return { cls, label: matchStatusLabel(status) };
    }

    function formatDateSectionTitle(ymd){
      if (ymd === "_unknown") return "日期待定";
      if (ymd.length < 10) return ymd;
      const y = Number(ymd.slice(0, 4)), mo = Number(ymd.slice(5, 7)), d = Number(ymd.slice(8, 10));
      if (!Number.isFinite(y) || !Number.isFinite(mo) || !Number.isFinite(d)) return ymd;
      const dt = new Date(y, mo - 1, d);
      const wk = ["周日","周一","周二","周三","周四","周五","周六"][dt.getDay()];
      return `${y}年${mo}月${d}日 ${wk}`;
    }

    function matchListItemHtml(m, idx){
      const sui = matchStatusUi(m.status);
      const st = venueStadium(m);
      const stadiumTxt = st ? `体育场：${esc(st)}` : "体育场：待公布";
      const raw = String(m.date || "").trim();
      const kick = raw.length >= 16 ? raw.slice(11, 16) : "";
      const metaLine = [String(m.round || "").trim(), kick || (raw.length > 10 ? raw : "")].filter(Boolean).join(" · ");
      return `
        <button type="button" data-match-index="${idx}" class="match-item mb-2 w-full rounded-lg border border-slate-700 bg-slate-900/60 p-2 text-left active:bg-slate-800/80 sm:p-3 sm:hover:border-accent/40">
          <div class="mb-1 flex flex-wrap items-center justify-between gap-1">
            <span class="min-w-0 flex-1 truncate text-sm text-slate-400 sm:text-base">${esc(metaLine || "—")}</span>
            <span class="shrink-0 rounded px-1.5 py-0.5 text-sm ${sui.cls} sm:px-2 sm:text-base">${esc(sui.label)}</span>
          </div>
          <div class="mb-1.5 truncate text-sm leading-snug text-slate-500 sm:text-base">${stadiumTxt}</div>
          <div class="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-1.5 sm:gap-3">
            <div class="flex min-w-0 items-center justify-end gap-1 sm:gap-2">
              <span class="min-w-0 truncate text-right text-sm font-semibold leading-tight text-slate-100 sm:text-base">${esc(m.home_club)}</span>
              ${clubImg(m.home_club)}
            </div>
            <div class="flex shrink-0 justify-center px-0.5">
              <div class="rounded-md bg-slate-800 px-2 py-1 font-mono text-base font-bold tabular-nums text-accent sm:px-3 sm:text-lg">${esc(fmtScore(m))}</div>
            </div>
            <div class="flex min-w-0 items-center justify-start gap-1 sm:gap-2">
              ${clubImg(m.away_club)}
              <span class="min-w-0 truncate text-left text-sm font-semibold leading-tight text-slate-100 sm:text-base">${esc(m.away_club)}</span>
            </div>
          </div>
        </button>
      `;
    }

    function renderMatchCalendar(matchDatesSet){
      if (!matchCalTitleEl || !matchCalCellsEl) return;
      const startYmd = calendarWindowStartYmd || sundayWeekContainingYmd(todayYmd());
      matchCalTitleEl.textContent = formatRangeTitle(startYmd, calendarRangeMode);
      if (matchCalRangeEl) matchCalRangeEl.value = calendarRangeMode;
      const todayKey = todayYmd();
      const cells = [];
      if (calendarRangeMode === "month"){
        const anchor = parseLocalYmd(startYmd) || parseLocalYmd(todayYmd());
        if (!anchor) return;
        const y = anchor.getFullYear();
        const m0 = anchor.getMonth();
        const first = new Date(y, m0, 1);
        const startPad = first.getDay();
        const lastDay = new Date(y, m0 + 1, 0).getDate();
        for (let i = 0; i < startPad; i++){
          cells.push('<div class="min-h-[1.5rem] sm:min-h-[1.75rem]" aria-hidden="true"></div>');
        }
        for (let d = 1; d <= lastDay; d++){
          const ymd = `${y}-${pad2(m0 + 1)}-${pad2(d)}`;
          const has = matchDatesSet.has(ymd);
          const isToday = ymd === todayKey;
          const isSel = selectedMatchDate === ymd;
          const isHl = calendarHighlightYmd && calendarHighlightYmd === ymd && !isSel;
          let cls = "relative flex min-h-[1.5rem] flex-col items-center justify-center rounded border text-[11px] font-medium transition-colors sm:min-h-[1.75rem] sm:rounded-md sm:text-xs";
          if (isSel) cls += " border-accent bg-accent/20 text-accent";
          else if (isHl) cls += " border-success/50 bg-success/10 text-success ring-1 ring-success/40";
          else cls += " border-transparent bg-slate-800/50 text-slate-200 hover:border-slate-600";
          if (isToday && !isSel && !isHl) cls += " ring-1 ring-accent/45";
          const dot = has ? '<span class="absolute bottom-0.5 h-0.5 w-0.5 rounded-full bg-accent sm:bottom-1 sm:h-1 sm:w-1" aria-hidden="true"></span>' : "";
          const dis = !has ? " opacity-60" : "";
          cells.push(`<button type="button" data-cal-date="${ymd}" class="${cls}${dis}" title="${has ? "查看当日比赛" : "当日无比赛"}">${d}${dot}</button>`);
        }
      } else {
        for (let i = 0; i < 7; i++){
          const ymd = addDaysYmd(startYmd, i);
          const dayNum = Number(ymd.slice(8, 10));
          const has = matchDatesSet.has(ymd);
          const isToday = ymd === todayKey;
          const isSel = selectedMatchDate === ymd;
          const isHl = calendarHighlightYmd && calendarHighlightYmd === ymd && !isSel;
          let cls = "relative flex min-h-[1.5rem] flex-col items-center justify-center rounded border text-[11px] font-medium transition-colors sm:min-h-[1.75rem] sm:rounded-md sm:text-xs";
          if (isSel) cls += " border-accent bg-accent/20 text-accent";
          else if (isHl) cls += " border-success/50 bg-success/10 text-success ring-1 ring-success/40";
          else cls += " border-transparent bg-slate-800/50 text-slate-200 hover:border-slate-600";
          if (isToday && !isSel && !isHl) cls += " ring-1 ring-accent/45";
          const dot = has ? '<span class="absolute bottom-0.5 h-0.5 w-0.5 rounded-full bg-accent sm:bottom-1 sm:h-1 sm:w-1" aria-hidden="true"></span>' : "";
          const dis = !has ? " opacity-60" : "";
          cells.push(`<button type="button" data-cal-date="${ymd}" class="${cls}${dis}" title="${has ? "查看当日比赛" : "当日无比赛"}">${dayNum}${dot}</button>`);
        }
      }
      matchCalCellsEl.innerHTML = cells.join("");
      matchCalCellsEl.querySelectorAll("[data-cal-date]").forEach(btn => {
        btn.addEventListener("click", () => {
          scheduleViewMode = "month";
          calendarHighlightYmd = "";
          selectedMatchDate = btn.getAttribute("data-cal-date") || "";
          renderMatches();
        });
      });
      if (matchCalResetDayBtn){
        matchCalResetDayBtn.textContent = calendarRangeMode === "month" ? "显示当月全部场次" : "显示本周全部场次";
        matchCalResetDayBtn.classList.toggle("hidden", !selectedMatchDate);
      }
      if (matchCalPrevBtn) matchCalPrevBtn.setAttribute("aria-label", calendarRangeMode === "month" ? "上月" : "上周");
      if (matchCalNextBtn) matchCalNextBtn.setAttribute("aria-label", calendarRangeMode === "month" ? "下月" : "下周");
    }

    function collectMatchClubNames(league){
      const names = new Set();
      for (const row of safeArr(league.standings)){
        const c = String(row.club_name || "").trim();
        if (c) names.add(c);
      }
      for (const m of safeArr(league.matches)){
        const h = String(m.home_club || "").trim();
        const a = String(m.away_club || "").trim();
        if (h) names.add(h);
        if (a) names.add(a);
      }
      return [...names].sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
    }

    function renderMatches(){
      const league = getLeague();
      if (matchClubFilterEl){
        const clubs = collectMatchClubNames(league);
        const want = selectedClub;
        matchClubFilterEl.innerHTML =
          '<option value="">全部俱乐部</option>' +
          clubs.map(c => `<option value="${optionValueAttr(c)}">${esc(c)}</option>`).join("");
        if (want && clubs.includes(want)){
          selectedClub = want;
          matchClubFilterEl.value = want;
        } else {
          selectedClub = "";
          matchClubFilterEl.value = "";
        }
      }
      let matches = safeArr(league.matches).slice();
      if (selectedClub){
        matches = matches.filter(m => m.home_club === selectedClub || m.away_club === selectedClub);
      }
      matches.sort((a,b)=> String(a.date||"").localeCompare(String(b.date||"")));

      const clubSig = selectedClub || "__all__";
      if (lastScheduleAnchorClubSig !== clubSig){
        matchViewNeedsDateAnchor = true;
        lastScheduleAnchorClubSig = clubSig;
      }
      if (matchViewNeedsDateAnchor && matches.length){
        selectedMatchDate = "";
        calendarHighlightYmd = "";
        matchScrollAnchorYmd = "";
        if (selectedClub){
          scheduleViewMode = "club_all";
          const ld = lastCompletedDateYmd(matches);
          if (ld && ld.length === 10){
            calendarWindowStartYmd = sundayWeekContainingYmd(ld);
            calendarHighlightYmd = ld;
            matchScrollAnchorYmd = ld;
          } else {
            const nu = firstUpcomingDateYmd(matches);
            if (nu && nu.length === 10){
              calendarWindowStartYmd = sundayWeekContainingYmd(nu);
              calendarHighlightYmd = nu;
            }
          }
        } else {
          scheduleViewMode = "global_adjacent";
          const tday = todayYmd();
          const dlist = [...new Set(matches.map(matchDateKey).filter(k => k.length === 10))].sort();
          const before = dlist.filter(d => d < tday);
          const after = dlist.filter(d => d > tday);
          const nextAround = after.length ? after[0] : "";
          const prevAround = before.length ? before[before.length - 1] : "";
          const pivot = nextAround || prevAround || nearestMatchDateYmd(matches);
          if (pivot && pivot.length === 10){
            calendarWindowStartYmd = sundayWeekContainingYmd(pivot);
          }
        }
        matchViewNeedsDateAnchor = false;
      }

      if (!calendarWindowStartYmd){
        calendarWindowStartYmd = sundayWeekContainingYmd(todayYmd());
      }

      const matchDatesSet = new Set();
      for (const m of matches){
        const k = matchDateKey(m);
        if (k.length === 10 && matchInCalendarRange(m, calendarWindowStartYmd, calendarRangeMode)){
          matchDatesSet.add(k);
        }
      }
      renderMatchCalendar(matchDatesSet);

      if (clearClubFilterBtn){
        clearClubFilterBtn.classList.toggle("hidden", !selectedClub);
      }

      const windowMatches = matches.filter(m => matchInCalendarRange(m, calendarWindowStartYmd, calendarRangeMode));
      let displayMatches;
      if (selectedMatchDate){
        displayMatches = matches.filter(m => matchDateKey(m) === selectedMatchDate);
      } else if (scheduleViewMode === "global_adjacent"){
        displayMatches = buildAdjacentMatchDaysWindow(matches);
        if (!displayMatches.length && matches.length){
          scheduleViewMode = "month";
          displayMatches = windowMatches.slice();
        }
      } else if (scheduleViewMode === "club_all"){
        displayMatches = matches.filter(m => isFinishedMatch(m) || isUpcomingMatch(m) || isLiveMatch(m));
        displayMatches.sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
      } else {
        displayMatches = windowMatches.slice();
      }

      if (!matches.length){
        matchListEl.innerHTML = '<div class="rounded-lg border border-slate-700 bg-slate-900/60 p-3 text-base text-slate-400">' +
          (selectedClub ? "该球队暂无比赛记录。" : "暂无赛程数据。") + "</div>";
        return;
      }

      if (selectedMatchDate && !displayMatches.length){
        const resetLabel = calendarRangeMode === "month" ? "显示当月全部场次" : "显示本周全部场次";
        matchListEl.innerHTML = `<div class="rounded-lg border border-slate-700 bg-slate-900/60 p-3 text-base text-slate-400">该日暂无比赛。可换一天或点击「${resetLabel}」。</div>`;
        return;
      }

      if (!selectedMatchDate && scheduleViewMode === "month" && !windowMatches.length){
        const rangeLabel = calendarRangeMode === "month" ? "当前月份" : "当前周";
        const navHint = calendarRangeMode === "month" ? "可切换上/下月查看其他日期" : "可切换上/下周查看其他日期";
        matchListEl.innerHTML = `<div class="rounded-lg border border-slate-700 bg-slate-900/60 p-3 text-base text-slate-400">${rangeLabel}暂无比赛（${navHint}）。</div>`;
        return;
      }

      if (!selectedMatchDate && !displayMatches.length){
        if (scheduleViewMode === "club_all"){
          matchListEl.innerHTML = '<div class="rounded-lg border border-slate-700 bg-slate-900/60 p-3 text-base text-slate-400">该俱乐部暂无已结束或未开始的比赛。</div>';
        } else {
          matchListEl.innerHTML = '<div class="rounded-lg border border-slate-700 bg-slate-900/60 p-3 text-base text-slate-400">暂无可展示的场次。</div>';
        }
        return;
      }

      const scrollDk = matchScrollAnchorYmd;
      let html = "";
      const flat = [];
      if (selectedMatchDate){
        displayMatches.forEach(m => {
          const idx = flat.length;
          flat.push(m);
          html += matchListItemHtml(m, idx);
        });
      } else {
        const dateGroups = new Map();
        for (const m of displayMatches){
          const dk = matchDateKey(m) || "_unknown";
          if (!dateGroups.has(dk)) dateGroups.set(dk, []);
          dateGroups.get(dk).push(m);
        }
        const order = [...dateGroups.keys()].sort((a, b) => {
          if (a === "_unknown") return 1;
          if (b === "_unknown") return -1;
          return a.localeCompare(b);
        });
        for (const dk of order){
          const list = dateGroups.get(dk);
          if (!list.length) continue;
          const anchorId = (scheduleViewMode === "club_all" && scrollDk && dk === scrollDk) ? ' id="schedule-club-anchor"' : "";
          html += `<div${anchorId} class="sticky top-0 z-[1] mb-2 border-b border-slate-700 bg-slate-900/95 py-1.5 pl-0.5 text-sm font-semibold text-accent backdrop-blur-sm">${esc(formatDateSectionTitle(dk))}</div>`;
          for (const m of list){
            const idx = flat.length;
            flat.push(m);
            html += matchListItemHtml(m, idx);
          }
        }
      }

      matchListEl.innerHTML = html;
      matchListEl.querySelectorAll(".match-item").forEach(btn => {
        btn.addEventListener("click", () => {
          const idx = Number(btn.dataset.matchIndex);
          openMatchModal(flat[idx]);
        });
      });
      if (scrollDk && scheduleViewMode === "club_all"){
        const el = document.getElementById("schedule-club-anchor");
        if (el){
          requestAnimationFrame(() => { el.scrollIntoView({ block: "center", behavior: "smooth" }); });
        }
      }
      matchScrollAnchorYmd = "";
    }

    function eventBadge(type){
      if (type === "goal") return "bg-success/20 text-success";
      if (type === "assist") return "bg-accent/20 text-accent";
      if (type === "yellow_card") return "bg-warn/20 text-warn";
      return "bg-danger/20 text-danger";
    }

    function buildPlayerStats(matches){
      const map = new Map();
      const teamVotes = new Map();
      const bumpTeam = (playerName, team) => {
        const t = String(team || "").trim();
        if (!t) return;
        if (!teamVotes.has(playerName)) teamVotes.set(playerName, new Map());
        const vm = teamVotes.get(playerName);
        vm.set(t, (vm.get(t) || 0) + 1);
      };
      const touch = (name) => {
        if (!map.has(name)) map.set(name, {player_name:name, team_name:"", goals:0, assists:0, yellow_card:0, red_card:0, matches:0});
        return map.get(name);
      };
      for (const m of safeArr(matches)){
        const seenInMatch = new Set();
        for (const e of safeArr(m.events)){
          let name = eventPlayerName(e);
          if (!name) continue;
          const team = String(e.team_name ?? e.teamName ?? e.club_name ?? "").trim();
          bumpTeam(name, team);
          const p = touch(name);
          if (e.type === "goal") p.goals += 1;
          if (e.type === "assist") p.assists += 1;
          if (e.type === "yellow_card") p.yellow_card += 1;
          if (e.type === "red_card") p.red_card += 1;
          seenInMatch.add(name);
        }
        seenInMatch.forEach(name => touch(name).matches += 1);
      }
      for (const p of map.values()){
        const vm = teamVotes.get(p.player_name);
        if (!vm || !vm.size) continue;
        let best = "", bestC = 0;
        vm.forEach((c, t) => { if (c > bestC){ bestC = c; best = t; } });
        if (best) p.team_name = best;
      }
      return [...map.values()].sort((a,b)=> (b.goals-a.goals) || (a.red_card-b.red_card) || a.player_name.localeCompare(b.player_name));
    }

    function buildPlayerStatsFromNormalized(){
      const merged = new Map();
      for (const row of safeArr(RAW_PLAYER_STATS)){
        const name = String(row.player_name || row.name || row.playerName || "").trim();
        if (!name || name === "Unknown" || name.startsWith("未命名球员")) continue;
        const team = String(row.team_name || "").trim();
        const key = name;
        const candidate = {
          player_name: name,
          team_name: team,
          goals: n(row.goals),
          assists: n(row.assists),
          yellow_card: n(row.yellow_cards ?? row.yellow_card),
          red_card: n(row.red_cards ?? row.red_card),
          matches: 0,
        };
        const prev = merged.get(key);
        if (!prev){
          merged.set(key, candidate);
          continue;
        }
        const prevScore = (prev.team_name ? 2 : 0) + prev.goals + prev.assists + prev.yellow_card + prev.red_card;
        const curScore = (candidate.team_name ? 2 : 0) + candidate.goals + candidate.assists + candidate.yellow_card + candidate.red_card;
        if (curScore > prevScore) merged.set(key, candidate);
      }
      return [...merged.values()].sort((a,b)=> (b.goals-a.goals) || (b.assists-a.assists) || (a.red_card-b.red_card) || a.player_name.localeCompare(b.player_name));
    }

    function showPlayersListSubview(){
      if (playersSubviewListEl) playersSubviewListEl.classList.remove("hidden");
      if (playersSubviewProfileEl) playersSubviewProfileEl.classList.add("hidden");
    }

    function absImgUrl(u){
      if (!u) return "";
      const s = String(u).trim();
      if (s.startsWith("//")) return "https:" + s;
      return s;
    }

    function teamRoughMatch(statTeam, row){
      const a = String(statTeam || "").trim();
      const club = String(row.contestant_club_name || "").trim();
      const shortN = String(row.contestant_short_name || "").trim();
      const longN = String(row.contestant_name || "").trim();
      if (!a) return true;
      if (club && (a === club || a.includes(club) || club.includes(a))) return true;
      if (shortN && (a === shortN || a.includes(shortN) || shortN.includes(a))) return true;
      if (longN && (a === longN || a.includes(longN) || longN.includes(a))) return true;
      return false;
    }

    function resolvePlayerClub(player, prof){
      const fromStats = String(player.team_name || "").trim();
      if (prof){
        const primary = String(prof.contestant_club_name || "").trim();
        const official = String(prof.contestant_name || "").trim();
        const shortN = String(prof.contestant_short_name || "").trim();
        if (primary){
          const sub = official && official !== primary ? official : "";
          return { display: primary, sub, shortLabel: shortN };
        }
        if (official) return { display: official, sub: "", shortLabel: shortN };
        if (shortN) return { display: shortN, sub: "", shortLabel: "" };
      }
      return { display: fromStats || "—", sub: "", shortLabel: "" };
    }

    function findCflProfile(player){
      const rows = safeArr(RAW_CFL_PROFILES);
      const name = String(player.player_name || "").trim();
      const team = String(player.team_name || "").trim();
      if (!name) return null;
      const byName = rows.filter(r => String(r.player_name || "").trim() === name);
      if (byName.length === 1) return byName[0];
      const withTeam = byName.filter(r => teamRoughMatch(team, r));
      if (withTeam.length === 1) return withTeam[0];
      if (withTeam.length > 0) return withTeam[0];
      return byName[0] || null;
    }

    function listPlayerClubDisplay(p){
      const prof = findCflProfile(p);
      return resolvePlayerClub(p, prof).display;
    }

    function showPlayerProfileSubview(player){
      if (!player) return;
      if (playersSubviewListEl) playersSubviewListEl.classList.add("hidden");
      if (playersSubviewProfileEl) playersSubviewProfileEl.classList.remove("hidden");
      const prof = findCflProfile(player);
      if (playerProfileTitleEl){
        playerProfileTitleEl.textContent = prof
          ? `${player.player_name} · 中足联注册信息（赛季 ${prof.tournament_calendar_name || ""}）`
          : `${player.player_name} · 赛季汇总（未匹配到注册档案时可先运行 CFL 球员同步）`;
      }
      const icon = prof ? absImgUrl(prof.player_icon || prof.clubIcon || prof.contestant_icon) : "";
      const clubInfo = resolvePlayerClub(player, prof);
      let html = "";
      html += `<div class="mb-4 rounded-xl border border-accent/35 bg-gradient-to-br from-slate-800/90 to-slate-900/80 p-3 sm:p-4">
        <div class="text-sm font-medium uppercase tracking-wide text-slate-500">所属俱乐部</div>
        <div class="mt-1 text-lg font-bold leading-snug text-slate-50 sm:text-xl">${esc(clubInfo.display)}</div>
        ${clubInfo.sub ? `<div class="mt-1 text-xs text-slate-400">${esc(clubInfo.sub)}</div>` : ""}
        ${clubInfo.shortLabel && clubInfo.display && !String(clubInfo.display).includes(clubInfo.shortLabel) ? `<div class="mt-0.5 text-sm text-accent">${esc(clubInfo.shortLabel)}</div>` : ""}
        ${prof && String(prof.contestant_club_name_en || prof.contestant_name_en || "").trim() ? `<div class="mt-1.5 text-xs italic text-slate-500">${esc(String(prof.contestant_club_name_en || prof.contestant_name_en || "").trim())}</div>` : ""}
      </div>`;
      html += `<div class="mb-4 rounded-lg border border-slate-700 bg-slate-800/60 p-3">
        <div class="mb-2 font-semibold text-slate-200">本页赛季统计</div>
        <ul class="grid grid-cols-2 gap-2 text-sm text-slate-300 sm:grid-cols-3 sm:text-base">
          <li>进球 <span class="font-mono text-accent">${n(player.goals)}</span></li>
          <li>助攻 <span class="font-mono text-accent">${n(player.assists)}</span></li>
          <li>出场 <span class="font-mono text-accent">${n(player.matches)}</span></li>
          <li>黄牌 <span class="font-mono text-warn">${n(player.yellow_card)}</span></li>
          <li>红牌 <span class="font-mono text-danger">${n(player.red_card)}</span></li>
        </ul></div>`;
      if (icon){
        html += `<div class="mb-4 flex justify-center"><img src="${esc(icon)}" alt="" class="h-28 w-28 rounded-xl border border-slate-600 bg-slate-900 object-cover sm:h-36 sm:w-36" loading="lazy" onerror="this.style.display='none'" /></div>`;
      }
      if (prof){
        const rows = [
          ["位置", prof.position_name || prof.position],
          ["号码", prof.player_shirt_number],
          ["身高 / 体重", [prof.height, prof.weight].filter(Boolean).join(" / ")],
          ["国籍", prof.nationality],
          ["出生日期", prof.date_of_birth],
          ["英文名", prof.player_name_en || prof.playerNameEn],
          ["注册状态", prof.player_status],
          ["球员 ID", prof.player_id],
        ];
        html += `<dl class="space-y-2">`;
        for (const pair of rows){
          const k = pair[0];
          const v = pair[1];
          if (v === undefined || v === null || v === "") continue;
          html += `<div class="flex gap-2 border-b border-slate-800/80 pb-2 text-base"><dt class="w-28 shrink-0 text-slate-500">${esc(k)}</dt><dd class="min-w-0 break-words text-slate-200">${esc(String(v))}</dd></div>`;
        }
        html += `</dl>`;
      } else {
        html += `<p class="text-base leading-relaxed text-slate-400">未在 <code class="rounded bg-slate-800 px-1 text-sm">cfl_players_page_raw.json</code> 中匹配到该球员。部署流水线中运行 <code class="rounded bg-slate-800 px-1 text-sm">cfl_players_page_crawler.py</code> 后可显示位置、号码、头像等注册信息。</p>`;
      }
      if (playerProfileBodyEl) playerProfileBodyEl.innerHTML = html;
    }

    function renderPlayers(){
      const league = getLeague();
      let players = buildPlayerStatsFromNormalized();
      if (!players.length){
        players = buildPlayerStats(league.matches);
      }
      const clubLabels = [...new Set(players.map(p => listPlayerClubDisplay(p)))].filter(c => c && c !== "—");
      clubLabels.sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
      if (playerClubFilterEl){
        const prev = (selectedPlayerClub || playerClubFilterEl.value || "").trim();
        playerClubFilterEl.innerHTML =
          '<option value="">全部俱乐部</option>' +
          clubLabels.map(c => `<option value="${optionValueAttr(c)}">${esc(c)}</option>`).join("");
        if (prev && clubLabels.includes(prev)){
          playerClubFilterEl.value = prev;
          selectedPlayerClub = prev;
        } else {
          playerClubFilterEl.value = "";
          selectedPlayerClub = "";
        }
      }
      const sel = (playerClubFilterEl && playerClubFilterEl.value) ? playerClubFilterEl.value.trim() : "";
      const filtered = sel ? players.filter(p => listPlayerClubDisplay(p) === sel) : players.slice();
      if (!filtered.length){
        playerListEl.innerHTML = '<div class="rounded-lg border border-slate-700 bg-slate-900/50 p-3 text-base text-slate-400">该筛选下暂无球员</div>';
        return;
      }
      playerListEl.innerHTML = filtered.map((p, idx) => {
        const clubLine = listPlayerClubDisplay(p);
        return `
        <div class="player-row mb-2 w-full rounded-lg border border-slate-700 bg-slate-900/60 p-2 sm:p-3">
          <div class="flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
            <span class="font-mono text-sm text-slate-500">${idx + 1}.</span>
            <button type="button" class="player-name-open-profile shrink-0 text-left text-base font-semibold text-accent underline-offset-2 hover:underline" data-player-index="${idx}">${esc(p.player_name)}</button>
            <span class="text-slate-600" aria-hidden="true">·</span>
            <span class="min-w-0 max-w-[min(100%,14rem)] truncate text-base text-slate-400 sm:max-w-[20rem]" title="所属俱乐部">${esc(clubLine)}</span>
          </div>
          <div class="mt-1 text-sm text-slate-300 sm:text-base">进球 ${p.goals} · 助攻 ${n(p.assists)} · 黄 ${p.yellow_card} · 红 ${p.red_card}</div>
        </div>`;
      }).join("");

      playerListEl.querySelectorAll(".player-name-open-profile").forEach((btn) => {
        btn.addEventListener("click", () => {
          const idx = Number(btn.dataset.playerIndex);
          showPlayerProfileSubview(filtered[idx]);
        });
      });
    }

    function switchView(name){
      currentView = name;
      if (name === "players"){
        showPlayersListSubview();
      }
      viewPanels.forEach(panel => panel.classList.toggle("hidden", panel.id !== `view-${name}`));
      viewButtons.forEach(btn => {
        const active = btn.dataset.view === name;
        const mobileNav = btn.closest("nav");
        if (mobileNav && mobileNav.classList.contains("lg:hidden")) {
          btn.classList.toggle("border-t-accent", active);
          btn.classList.toggle("border-transparent", !active);
          btn.classList.toggle("text-accent", active);
          btn.classList.toggle("text-slate-400", !active);
          btn.classList.toggle("bg-accent/10", active);
          return;
        }
        btn.classList.toggle("bg-accent/15", active);
        btn.classList.toggle("border-accent/40", active);
        btn.classList.toggle("text-accent", active);
        btn.classList.toggle("bg-slate-800/70", !active);
        btn.classList.toggle("border-slate-700", !active);
        btn.classList.toggle("text-slate-200", !active);
      });
    }

    function renderAll(){
      renderLeagueNav();
      renderOfficialPolicy();
      renderStandings();
      renderMatches();
      renderPlayers();
      switchView(currentView);
    }

    if (sortOfficialBtn) sortOfficialBtn.addEventListener("click", () => {
      standingsSortMode = "official";
      renderStandings();
    });
    if (sortMatchBtn) sortMatchBtn.addEventListener("click", () => {
      standingsSortMode = "match";
      renderStandings();
    });

    viewButtons.forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));
    if (playerClubFilterEl) playerClubFilterEl.addEventListener("change", () => {
      selectedPlayerClub = (playerClubFilterEl.value || "").trim();
      renderPlayers();
    });
    if (backToPlayerListBtn) backToPlayerListBtn.addEventListener("click", showPlayersListSubview);
    clearClubFilterBtn.addEventListener("click", () => {
      selectedClub = "";
      selectedMatchDate = "";
      scheduleViewMode = "month";
      calendarHighlightYmd = "";
      matchScrollAnchorYmd = "";
      calendarWindowStartYmd = sundayWeekContainingYmd(todayYmd());
      if (matchClubFilterEl) matchClubFilterEl.value = "";
      renderMatches();
    });
    if (matchClubFilterEl) matchClubFilterEl.addEventListener("change", () => {
      selectedClub = (matchClubFilterEl.value || "").trim();
      selectedMatchDate = "";
      renderMatches();
    });
    if (matchCalRangeEl) matchCalRangeEl.addEventListener("change", () => {
      const v = (matchCalRangeEl.value || "").trim();
      calendarRangeMode = (v === "month") ? "month" : "week";
      selectedMatchDate = "";
      scheduleViewMode = "month";
      calendarHighlightYmd = "";
      matchScrollAnchorYmd = "";
      if (!calendarWindowStartYmd){
        calendarWindowStartYmd = sundayWeekContainingYmd(todayYmd());
      }
      renderMatches();
    });
    if (matchCalPrevBtn) matchCalPrevBtn.addEventListener("click", () => {
      selectedMatchDate = "";
      scheduleViewMode = "month";
      calendarHighlightYmd = "";
      matchScrollAnchorYmd = "";
      const cur = calendarWindowStartYmd || sundayWeekContainingYmd(todayYmd());
      calendarWindowStartYmd = calendarRangeMode === "month" ? shiftMonthYmd(cur, -1) : addDaysYmd(cur, -7);
      renderMatches();
    });
    if (matchCalNextBtn) matchCalNextBtn.addEventListener("click", () => {
      selectedMatchDate = "";
      scheduleViewMode = "month";
      calendarHighlightYmd = "";
      matchScrollAnchorYmd = "";
      const cur = calendarWindowStartYmd || sundayWeekContainingYmd(todayYmd());
      calendarWindowStartYmd = calendarRangeMode === "month" ? shiftMonthYmd(cur, 1) : addDaysYmd(cur, 7);
      renderMatches();
    });
    if (matchCalResetDayBtn) matchCalResetDayBtn.addEventListener("click", () => {
      selectedMatchDate = "";
      scheduleViewMode = "month";
      calendarHighlightYmd = "";
      matchScrollAnchorYmd = "";
      renderMatches();
    });
    closeModalBtn.addEventListener("click", closeMatchModal);
    matchModalOverlay.addEventListener("click", closeMatchModal);
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!matchModal.classList.contains("hidden")){
        closeMatchModal();
        return;
      }
      if (currentView === "players" && playersSubviewProfileEl && !playersSubviewProfileEl.classList.contains("hidden")){
        showPlayersListSubview();
      }
    });

    /** 与 dashboard_embed.json 同目录；兼容 GitHub Pages 项目站无末尾 / 的地址（避免解析到站点根导致 404） */
    function resolveEmbedJsonUrl(){
      const p = window.location.pathname || "/";
      if (p.endsWith("/")) return new URL("dashboard_embed.json", window.location.origin + p);
      const last = p.slice(p.lastIndexOf("/") + 1);
      if (last.includes(".") && /\\.[a-z0-9]+$/i.test(last)){
        return new URL("dashboard_embed.json", window.location.origin + p.slice(0, p.lastIndexOf("/") + 1));
      }
      return new URL("dashboard_embed.json", window.location.origin + p + "/");
    }

    let lastEmbedFetchAt = 0;
    const EMBED_REFRESH_THROTTLE_MS = 30000;
    const EMBED_POLL_MS = 120000;

    async function fetchDashboardBundle(){
      const embedUrl = resolveEmbedJsonUrl();
      embedUrl.searchParams.set("v", String(Date.now()));
      const res = await fetch(embedUrl.toString(), {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" }
      });
      if (!res.ok) throw new Error("dashboard_embed.json HTTP " + res.status);
      return res.json();
    }

    function applyDashboardBundle(bundle){
      RAW_DATA = bundle.raw_data || { leagues: [] };
      RAW_PLAYER_STATS = Array.isArray(bundle.player_stats) ? bundle.player_stats : [];
      TEAM_LOGOS = bundle.team_logos && typeof bundle.team_logos === "object" ? bundle.team_logos : {};
      RAW_CFL_PROFILES = Array.isArray(bundle.cfl_player_profiles) ? bundle.cfl_player_profiles : [];
    }

    async function refreshDashboardData(opts){
      const o = opts || {};
      const silent = !!o.silent;
      try {
        const bundle = await fetchDashboardBundle();
        applyDashboardBundle(bundle);
        lastEmbedFetchAt = Date.now();
        renderAll();
      } catch (err){
        console.warn("refreshDashboardData failed", err);
        if (!silent){
          console.error("embed base", resolveEmbedJsonUrl().toString());
        }
      }
    }

    async function bootDashboard(){
      let triedUrl = "";
      try {
        const bundle = await fetchDashboardBundle();
        triedUrl = resolveEmbedJsonUrl().toString();
        applyDashboardBundle(bundle);
        lastEmbedFetchAt = Date.now();
        renderAll();
      } catch (err) {
        console.error("bootDashboard failed, url=", triedUrl, err);
        const bar = document.createElement("div");
        bar.className = "fixed left-0 right-0 top-0 z-[100] bg-red-900 px-3 py-2 text-center text-sm text-white";
        bar.textContent = "数据加载失败（请检查是否已部署 dashboard_embed.json，或强制刷新 Ctrl+F5；控制台可见请求 URL）";
        document.body.insertBefore(bar, document.body.firstChild);
      }
    }

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastEmbedFetchAt < EMBED_REFRESH_THROTTLE_MS) return;
      refreshDashboardData({ silent: true });
    });

    setInterval(() => {
      if (document.visibilityState !== "visible") return;
      refreshDashboardData({ silent: true });
    }, EMBED_POLL_MS);

    bootDashboard();
  </script>
</body>
</html>
"""

    html = html.replace("__GENERATED__", generated_at)
    html = html.replace("__SOURCE_FILE__", source_file)
    return html


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    web_dir = root / "web"
    output_path = web_dir / "index.html"
    data = load_data(root)
    data["_normalized_player_stats"] = load_normalized_player_stats(root)
    merged = json.loads(json.dumps(data))
    merged = _merge_same_competition(merged)
    club_names = _collect_club_names(merged)
    team_logos = build_team_logo_map_for_dashboard(root, club_names)
    pub_base = os.environ.get("CSL_DASHBOARD_PUBLIC_BASE", "").strip()
    if pub_base:
        team_logos = apply_team_logo_public_base(team_logos, pub_base)

    lean, stats, logos_embed, source_file = prepare_dashboard_embed_payload(data, team_logos)
    cfl_profiles = load_cfl_player_profiles_for_embed(root)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = build_dashboard_html(source_file, generated_at)

    bundle = {
        "raw_data": lean,
        "player_stats": stats,
        "team_logos": logos_embed,
        "cfl_player_profiles": cfl_profiles,
    }
    embed_path = web_dir / "dashboard_embed.json"
    web_dir.mkdir(parents=True, exist_ok=True)
    embed_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    output_path.write_text(html, encoding="utf-8")
    print(f"dashboard written: {output_path}")
    print(f"embed bundle written: {embed_path}")


if __name__ == "__main__":
    main()
