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


def build_dashboard_html(source_file: str, generated_at: str, embed_cache_bust: str) -> str:
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
  </style>
</head>
<body class="bg-midnight text-slate-100 min-h-screen touch-manipulation pb-[max(4.5rem,calc(3.75rem+env(safe-area-inset-bottom,0px)))] lg:pb-0">
  <div class="mx-auto max-w-[1600px] p-2 sm:p-4 md:p-6">
    <header class="mb-3 rounded-2xl border border-slate-800 bg-surface/90 p-3 sm:p-4 md:p-6">
      <div class="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div class="min-w-0">
          <p class="text-[10px] uppercase tracking-[0.15em] text-accent sm:text-xs sm:tracking-[0.2em]">China Football Season Monitor 2026</p>
          <h1 class="text-xl font-black sm:text-2xl md:text-3xl">Ultimate CSL Dashboard</h1>
          <p class="hidden text-sm text-slate-400 sm:block">Match timeline drilldown + penalty-adjusted standings + CFL player profiles</p>
          <div id="leagueNavMobile" class="mt-2 lg:hidden"></div>
        </div>
        <div class="hidden text-xs text-slate-400 rounded-md border border-slate-700 bg-slate-900/70 px-3 py-2 sm:block">
          Generated: __GENERATED__<br/>Source: __SOURCE_FILE__
        </div>
      </div>
    </header>

    <div class="grid grid-cols-1 gap-3 sm:gap-4 lg:grid-cols-[260px_1fr]">
      <aside class="hidden rounded-2xl border border-slate-800 bg-surface/90 p-4 lg:block">
        <h2 class="mb-3 text-sm font-bold uppercase tracking-wide text-slate-300">League</h2>
        <div id="leagueNav" class="space-y-2"></div>

        <h2 class="mt-6 mb-3 text-sm font-bold uppercase tracking-wide text-slate-300">视图</h2>
        <div class="space-y-2">
          <button type="button" data-view="standings" class="view-btn view-btn-desktop w-full rounded-lg border border-accent/40 bg-accent/15 px-3 py-2 text-left text-sm font-semibold text-accent">积分榜</button>
          <button type="button" data-view="matches" class="view-btn view-btn-desktop w-full rounded-lg border border-slate-700 bg-slate-800/70 px-3 py-2 text-left text-sm font-semibold text-slate-200">赛程与事件</button>
          <button type="button" data-view="players" class="view-btn view-btn-desktop w-full rounded-lg border border-slate-700 bg-slate-800/70 px-3 py-2 text-left text-sm font-semibold text-slate-200">球员榜</button>
        </div>
      </aside>

      <main class="space-y-4">
        <section id="view-standings" class="view-panel rounded-2xl border border-slate-800 bg-surface/90 p-3 sm:p-4">
          <div class="mb-2 flex flex-col gap-2 sm:mb-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
            <h2 class="text-base font-bold sm:text-lg">积分榜</h2>
            <div class="flex flex-wrap gap-2">
              <button type="button" id="sortOfficialBtn" class="sort-standings-btn min-h-[40px] rounded-lg border border-accent/50 bg-accent/15 px-3 py-2 text-xs font-semibold text-accent sm:min-h-0 sm:text-sm">
                按官方积分排序
              </button>
              <button type="button" id="sortMatchBtn" class="sort-standings-btn min-h-[40px] rounded-lg border border-slate-600 bg-slate-800/80 px-3 py-2 text-xs font-semibold text-slate-200 sm:min-h-0 sm:text-sm">
                按赛场积分排序
              </button>
            </div>
          </div>
          <details id="officialPolicyDetails" class="mb-3 rounded-lg border border-slate-700 bg-slate-900/50 text-xs text-slate-400 sm:text-sm">
            <summary class="cursor-pointer select-none px-3 py-2 font-semibold text-slate-300 hover:text-accent">
              官方积分说明（中国足协 2026 赛前纪律扣分）
            </summary>
            <div id="officialPolicyBody" class="space-y-2 border-t border-slate-800 px-3 py-2 leading-relaxed"></div>
          </details>
          <div class="overflow-x-auto overscroll-x-contain -mx-1 px-1 sm:mx-0 sm:px-0">
            <table class="min-w-[720px] w-full text-[11px] sm:min-w-0 sm:text-sm">
              <thead class="bg-slate-900/60 text-slate-300">
                <tr>
                  <th class="sticky left-0 z-20 bg-slate-900/95 px-1 py-2 text-left shadow-[2px_0_8px_rgba(0,0,0,0.3)] sm:px-2">#</th>
                  <th class="sticky left-7 z-20 bg-slate-900/95 px-1 py-2 text-left shadow-[2px_0_8px_rgba(0,0,0,0.3)] sm:left-8 sm:px-2">球队</th>
                  <th class="px-1 py-2 text-left sm:px-2">赛</th>
                  <th class="px-1 py-2 text-left sm:px-2">胜-平-负</th>
                  <th class="px-1 py-2 text-left sm:px-2">近况</th>
                  <th class="px-1 py-2 text-left sm:px-2">进</th>
                  <th class="px-1 py-2 text-left sm:px-2">失</th>
                  <th class="px-1 py-2 text-left sm:px-2">净</th>
                  <th class="px-1 py-2 text-left sm:px-2" title="仅由已赛场次胜平负累计">赛场</th>
                  <th class="px-1 py-2 text-left sm:px-2" title="赛季开赛前足协纪律处罚">赛前扣</th>
                  <th class="px-1 py-2 text-left sm:px-2" title="赛场积分 − 赛前扣分">官方</th>
                </tr>
              </thead>
              <tbody id="standingsBody"></tbody>
            </table>
          </div>
        </section>

        <section id="view-matches" class="view-panel hidden">
          <div class="rounded-2xl border border-slate-800 bg-surface/90 p-3 sm:p-4">
            <div class="mb-2 flex flex-col gap-2 sm:mb-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
              <h2 class="text-base font-bold sm:text-lg">赛程与事件</h2>
              <div id="clubFilterBar" class="hidden flex-col gap-2 sm:flex sm:flex-row sm:items-center">
                <span class="rounded-full border border-accent/40 bg-accent/15 px-3 py-1.5 text-xs text-accent">
                  筛选：<span id="clubFilterName"></span>
                </span>
                <button id="clearClubFilterBtn" type="button" class="min-h-[44px] rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-xs hover:border-accent/40 sm:min-h-0 sm:py-1">
                  清除筛选
                </button>
              </div>
            </div>
            <div id="matchList" class="max-h-[min(640px,calc(100dvh-11rem))] overflow-auto pr-1 sm:max-h-[640px]"></div>
          </div>
        </section>

        <section id="view-players" class="view-panel hidden space-y-3 sm:space-y-4">
          <div id="playersSubviewList" class="space-y-3 sm:space-y-4">
            <div class="rounded-2xl border border-slate-800 bg-surface/90 p-3 sm:p-4">
              <h2 class="mb-1 text-base font-bold sm:mb-2 sm:text-lg">球员榜</h2>
              <p class="mb-2 text-[11px] text-slate-500 sm:text-xs">点击球员姓名查看赛季数据与中足联注册档案（二级页面）</p>
              <div id="playerList" class="max-h-[min(480px,calc(100dvh-14rem))] overflow-auto pr-1 sm:max-h-[560px]"></div>
            </div>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div class="rounded-2xl border border-slate-800 bg-surface/90 p-3 sm:p-4">
                <h2 class="mb-2 text-base font-bold sm:text-lg">射手榜 <span class="text-xs font-normal text-slate-500">TOP 20</span></h2>
                <p class="mb-2 text-[11px] text-slate-500 sm:text-xs">按进球数排序；点击球员名查看档案</p>
                <div id="scorerRankList" class="max-h-[min(320px,50vh)] overflow-auto pr-1 sm:max-h-[380px]"></div>
              </div>
              <div class="rounded-2xl border border-slate-800 bg-surface/90 p-3 sm:p-4">
                <h2 class="mb-2 text-base font-bold sm:text-lg">助攻榜 <span class="text-xs font-normal text-slate-500">TOP 20</span></h2>
                <p class="mb-2 text-[11px] text-slate-500 sm:text-xs">按助攻数排序；点击球员名查看档案</p>
                <div id="assistRankList" class="max-h-[min(320px,50vh)] overflow-auto pr-1 sm:max-h-[380px]"></div>
              </div>
            </div>
          </div>
          <div id="playersSubviewProfile" class="hidden">
            <div class="rounded-2xl border border-slate-800 bg-surface/90 p-3 sm:p-4">
              <div class="mb-3 flex flex-wrap items-center gap-2">
                <button type="button" id="backToPlayerListBtn" class="min-h-[40px] rounded-lg border border-slate-600 bg-slate-800/80 px-3 py-2 text-sm font-semibold text-slate-200 hover:border-accent/40 hover:text-accent sm:min-h-0">
                  ← 返回球员榜
                </button>
              </div>
              <h2 class="mb-2 text-base font-bold sm:text-lg">球员档案</h2>
              <div id="playerProfileTitle" class="mb-3 text-xs text-slate-400 sm:text-sm"></div>
              <div id="playerProfileBody" class="max-h-[min(70dvh,560px)] overflow-y-auto pr-1 text-sm"></div>
            </div>
          </div>
        </section>
      </main>
    </div>
  </div>

  <nav class="fixed bottom-0 left-0 right-0 z-40 border-t border-slate-800 bg-slate-900/95 backdrop-blur-md lg:hidden" style="padding-bottom: env(safe-area-inset-bottom, 0px);">
    <div class="flex max-w-[1600px] mx-auto">
      <button type="button" data-view="standings" class="view-btn flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 border-t-2 border-transparent px-1 py-2 text-[10px] font-semibold text-slate-200 active:bg-slate-800/80">
        <span class="text-base leading-none">📊</span>
        <span>积分榜</span>
      </button>
      <button type="button" data-view="matches" class="view-btn flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 border-t-2 border-transparent px-1 py-2 text-[10px] font-semibold text-slate-200 active:bg-slate-800/80">
        <span class="text-base leading-none">⚽</span>
        <span>赛程</span>
      </button>
      <button type="button" data-view="players" class="view-btn flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 border-t-2 border-transparent px-1 py-2 text-[10px] font-semibold text-slate-200 active:bg-slate-800/80">
        <span class="text-base leading-none">📈</span>
        <span>球员</span>
      </button>
    </div>
  </nav>

  <div id="matchModal" class="fixed inset-0 z-50 hidden">
    <div id="matchModalOverlay" class="absolute inset-0 bg-black/60 backdrop-blur-[2px]"></div>
    <div class="absolute inset-x-2 bottom-2 flex h-[min(88dvh,calc(100dvh-env(safe-area-inset-bottom,0px)-1rem))] flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl sm:inset-x-auto sm:bottom-0 sm:left-auto sm:right-0 sm:top-0 sm:h-full sm:max-h-none sm:w-full sm:max-w-2xl sm:rounded-none sm:border-l sm:border-t-0">
      <div class="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 px-3 py-3 sm:px-4">
        <div class="min-w-0 flex-1 pr-1">
          <h3 class="text-sm font-bold sm:text-lg">比赛事件</h3>
          <div id="modalTitle" class="mt-1 min-w-0 text-xs sm:text-sm"></div>
        </div>
        <button type="button" id="closeModalBtn" class="min-h-[44px] shrink-0 rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm hover:border-accent/40 sm:min-h-0 sm:py-1">关闭</button>
      </div>
      <div id="modalTimeline" class="min-h-0 flex-1 overflow-y-auto overscroll-y-contain p-3 space-y-3 sm:p-4"></div>
    </div>
  </div>

  <script>
    /** 数据从同目录 dashboard_embed.json 加载，避免内联巨型 JSON 被 HTML/浏览器截断或误解析 */
    var RAW_DATA, RAW_PLAYER_STATS, TEAM_LOGOS, RAW_CFL_PROFILES;

    const leagueNavEl = document.getElementById("leagueNav");
    const leagueNavMobileEl = document.getElementById("leagueNavMobile");
    const standingsBodyEl = document.getElementById("standingsBody");
    const matchListEl = document.getElementById("matchList");
    const matchModal = document.getElementById("matchModal");
    const matchModalOverlay = document.getElementById("matchModalOverlay");
    const closeModalBtn = document.getElementById("closeModalBtn");
    const modalTitleEl = document.getElementById("modalTitle");
    const modalTimelineEl = document.getElementById("modalTimeline");
    const clubFilterBarEl = document.getElementById("clubFilterBar");
    const clubFilterNameEl = document.getElementById("clubFilterName");
    const clearClubFilterBtn = document.getElementById("clearClubFilterBtn");
    const playerListEl = document.getElementById("playerList");
    const scorerRankListEl = document.getElementById("scorerRankList");
    const assistRankListEl = document.getElementById("assistRankList");
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
    let standingsSortMode = "official";

    function safeArr(v){ return Array.isArray(v) ? v : []; }
    function n(v){ const x = Number(v); return Number.isFinite(x) ? x : 0; }
    function esc(s){ return String(s ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;"); }

    function eventPlayerName(e){
      if (!e || typeof e !== "object") return "";
      const raw = e.player ?? e.player_name ?? e.eventPlayerName ?? e.event_player_name ?? e.name ?? e.playerName ?? e.athleteName ?? e.athlete_name ?? e.label ?? "";
      return String(raw).trim();
    }

    function clubImg(name){
      const src = TEAM_LOGOS[name] || "";
      const initial = String(name || "").trim().slice(0, 1) || "?";
      const box = "inline-flex h-7 w-7 shrink-0 items-center justify-center sm:h-8 sm:w-8";
      if (!src){
        return `<span class="${box} rounded-md border border-slate-700 bg-slate-900 text-[10px] font-bold text-slate-500 sm:text-xs" title="${esc(name)}">${esc(initial)}</span>`;
      }
      return `<span class="${box} overflow-hidden rounded-md border border-slate-700 bg-slate-900">
        <img src="${esc(src)}" alt="" class="h-full w-full object-contain p-0.5" loading="lazy" onerror="this.style.display='none'; const f=this.nextElementSibling; if(f) f.classList.remove('hidden');" />
        <span class="hidden h-full w-full items-center justify-center text-[10px] font-bold text-slate-500 sm:text-xs">${esc(initial)}</span>
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

    function renderLeagueNav(){
      const league = getLeague();
      const chip = `<div class="w-full rounded-lg border border-accent/50 bg-accent/15 px-3 py-2 text-left text-sm font-semibold text-accent">${esc(league.name || "中超联赛")}</div>`;
      leagueNavEl.innerHTML = chip;
      if (leagueNavMobileEl) leagueNavMobileEl.innerHTML = chip;
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
      const standings = sortStandingsRows(safeArr(league.standings), standingsSortMode);
      const matches = safeArr(league.matches);
      updateSortButtons();
      standingsBodyEl.innerHTML = standings.map((row, idx) => {
        const wdl = safeArr(row.w_d_l);
        const form = computeForm(row.club_name, matches);
        return `
          <tr class="border-b border-slate-800 hover:bg-slate-800/50">
            <td class="sticky left-0 z-10 bg-slate-900/95 px-1 py-2 text-center shadow-[2px_0_8px_rgba(0,0,0,0.2)] sm:px-2">${idx + 1}</td>
            <td class="sticky left-7 z-10 max-w-[7.5rem] bg-slate-900/95 px-1 py-2 font-semibold shadow-[2px_0_8px_rgba(0,0,0,0.2)] sm:left-8 sm:max-w-none sm:px-2">
              <div class="flex items-center gap-1 sm:gap-2">
                ${clubImg(row.club_name)}
                <button type="button" data-club-name="${esc(row.club_name)}" class="club-filter-btn min-h-[40px] max-w-[5.5rem] rounded px-0.5 py-1 text-left text-[11px] leading-tight text-accent hover:bg-accent/10 sm:min-h-0 sm:max-w-none sm:px-1 sm:text-sm sm:leading-normal hover:underline">
                  ${esc(row.club_name)}
                </button>
              </div>
            </td>
            <td class="px-1 py-2 sm:px-2">${n(row.played)}</td>
            <td class="px-1 py-2 sm:px-2">
              <span class="inline-flex items-center gap-0.5 rounded bg-success/20 px-1 py-0.5 text-[10px] text-success sm:gap-1 sm:px-2 sm:text-xs">胜${n(wdl[0])}</span>
              <span class="inline-flex items-center gap-0.5 rounded bg-warn/20 px-1 py-0.5 text-[10px] text-warn sm:gap-1 sm:px-2 sm:text-xs">平${n(wdl[1])}</span>
              <span class="inline-flex items-center gap-0.5 rounded bg-danger/20 px-1 py-0.5 text-[10px] text-danger sm:gap-1 sm:px-2 sm:text-xs">负${n(wdl[2])}</span>
            </td>
            <td class="whitespace-nowrap px-1 py-2 sm:px-2">${form.map(resultEmoji).join(" ") || "—"}</td>
            <td class="px-1 py-2 sm:px-2">${n(row?.summary?.goals_for)}</td>
            <td class="px-1 py-2 sm:px-2">${n(row?.summary?.goals_against)}</td>
            <td class="px-1 py-2 sm:px-2 ${n(row.goal_difference) >= 0 ? "text-success" : "text-danger"}">${n(row.goal_difference)}</td>
            <td class="px-1 py-2 font-semibold text-slate-200 sm:px-2">${n(row.points)}</td>
            <td class="px-1 py-2 sm:px-2 ${n(row.penalty_points) > 0 ? "text-danger font-semibold" : "text-slate-300"}">-${n(row.penalty_points)}</td>
            <td class="px-1 py-2 font-bold text-accent sm:px-2">${n(row.effective_points)}</td>
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
        ? `<div class="mb-3 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-[11px] text-slate-300 sm:text-xs"><span class="text-slate-500">阵型</span> 主 ${esc(hf || "—")} · 客 ${esc(af || "—")}</div>`
        : "";
      modalTitleEl.innerHTML = `
        <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-slate-300 sm:gap-x-2">
          ${clubImg(home)}
          <span class="max-w-[min(42vw,9rem)] truncate text-xs font-semibold sm:max-w-none sm:text-sm" title="${esc(home)}">${esc(home)}</span>
          <span class="rounded bg-slate-800 px-2 py-1 font-mono text-sm font-bold text-accent sm:py-0.5">${esc(fmtScore(match))}</span>
          ${clubImg(away)}
          <span class="max-w-[min(42vw,9rem)] truncate text-xs font-semibold sm:max-w-none sm:text-sm" title="${esc(away)}">${esc(away)}</span>
          <span class="w-full shrink-0 text-[11px] text-slate-500 sm:ml-1 sm:w-auto sm:text-xs">· ${esc(match.date || "")}</span>
          <span class="w-full text-[11px] leading-snug text-slate-500 sm:text-xs">${stadiumLine}</span>
        </div>
      `;
      const events = safeArr(match.events).slice().sort((a,b)=>n(a.minute)-n(b.minute));
      let body = formLine;
      if (!events.length){
        body += '<div class="text-sm text-slate-400">本场比赛暂无事件数据（未赛或数据源未更新）。</div>';
      } else {
        body += events.map(e => `
          <div class="relative pl-6 sm:pl-7">
            <span class="absolute left-0 top-2 h-2 w-2 rounded-full bg-accent"></span>
            <div class="rounded-lg border border-slate-700 bg-slate-800/70 p-2.5 sm:p-3">
              <div class="mb-1 flex items-center justify-between gap-2">
                <span class="rounded px-2 py-0.5 text-[10px] sm:text-xs ${eventBadge(e.type)}">${esc(e.type)}</span>
                <span class="font-mono text-xs text-slate-300 sm:text-sm">${esc(e.minute)}'</span>
              </div>
              <div class="text-xs font-semibold sm:text-sm">${esc(eventPlayerName(e) || "未知球员")}</div>
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

    function renderMatches(){
      const league = getLeague();
      let matches = safeArr(league.matches).slice();
      if (selectedClub){
        matches = matches.filter(m => m.home_club === selectedClub || m.away_club === selectedClub);
      }
      matches.sort((a,b)=>{
        const fa = String(a.status||"").toLowerCase() === "finished" ? 0 : 1;
        const fb = String(b.status||"").toLowerCase() === "finished" ? 0 : 1;
        if (fa !== fb) return fa - fb;
        return String(a.date||"").localeCompare(String(b.date||""));
      });

      if (selectedClub){
        clubFilterBarEl.classList.remove("hidden");
        clubFilterBarEl.classList.add("flex");
        clubFilterNameEl.textContent = selectedClub;
      } else {
        clubFilterBarEl.classList.add("hidden");
        clubFilterBarEl.classList.remove("flex");
        clubFilterNameEl.textContent = "";
      }

      matchListEl.innerHTML = matches.map((m, idx) => {
        const status = String(m.status || "").toLowerCase();
        const statusCls = status === "finished" ? "bg-success/20 text-success" : "bg-warn/20 text-warn";
        const st = venueStadium(m);
        const stadiumTxt = st ? `体育场：${esc(st)}` : "体育场：待公布";
        return `
          <button type="button" data-match-index="${idx}" class="match-item mb-2 w-full rounded-lg border border-slate-700 bg-slate-900/60 p-2.5 text-left active:bg-slate-800/80 sm:p-3 sm:hover:border-accent/40">
            <div class="mb-1 flex flex-wrap items-center justify-between gap-1">
              <span class="text-[11px] text-slate-400 sm:text-xs">${esc(m.round || "")} · ${esc(m.date || "")}</span>
              <span class="rounded px-2 py-0.5 text-[10px] ${statusCls} sm:text-xs">${esc(status || "scheduled")}</span>
            </div>
            <div class="mb-1.5 text-[11px] leading-snug text-slate-500 sm:text-xs">${stadiumTxt}</div>
            <div class="grid grid-cols-1 items-center gap-2 min-[380px]:grid-cols-[1fr_auto_1fr]">
              <div class="flex items-center justify-center gap-2 min-w-0 min-[380px]:justify-end">
                <span class="max-w-[45%] truncate text-right text-xs font-medium sm:text-sm">${esc(m.home_club)}</span>
                ${clubImg(m.home_club)}
              </div>
              <div class="flex justify-center">
                <div class="rounded bg-slate-800 px-3 py-1.5 font-mono text-base font-bold text-accent sm:px-2 sm:py-1 sm:text-sm">${esc(fmtScore(m))}</div>
              </div>
              <div class="flex items-center justify-center gap-2 min-w-0 min-[380px]:justify-start">
                ${clubImg(m.away_club)}
                <span class="max-w-[45%] truncate text-xs font-medium sm:text-sm">${esc(m.away_club)}</span>
              </div>
            </div>
          </button>
        `;
      }).join("");

      const items = document.querySelectorAll(".match-item");
      items.forEach(btn => {
        btn.addEventListener("click", () => {
          const idx = Number(btn.dataset.matchIndex);
          openMatchModal(matches[idx]);
        });
      });

      if (!matches.length){
        matchListEl.innerHTML = '<div class="rounded-lg border border-slate-700 bg-slate-900/60 p-3 text-sm text-slate-400">该球队暂无比赛记录。</div>';
      }
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
      const shortN = String(row.contestant_short_name || "").trim();
      const longN = String(row.contestant_name || "").trim();
      if (!a) return true;
      if (shortN && (a === shortN || a.includes(shortN) || shortN.includes(a))) return true;
      if (longN && (a === longN || a.includes(longN) || longN.includes(a))) return true;
      return false;
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
      let html = "";
      html += `<div class="mb-4 rounded-lg border border-slate-700 bg-slate-800/60 p-3">
        <div class="mb-2 font-semibold text-slate-200">本页赛季统计</div>
        <ul class="grid grid-cols-2 gap-2 text-xs text-slate-300 sm:grid-cols-3 sm:text-sm">
          <li>进球 <span class="font-mono text-accent">${n(player.goals)}</span></li>
          <li>助攻 <span class="font-mono text-accent">${n(player.assists)}</span></li>
          <li>出场 <span class="font-mono text-accent">${n(player.matches)}</span></li>
          <li>黄牌 <span class="font-mono text-warn">${n(player.yellow_card)}</span></li>
          <li>红牌 <span class="font-mono text-danger">${n(player.red_card)}</span></li>
          <li>球队 <span class="text-slate-200">${esc(player.team_name || "—")}</span></li>
        </ul></div>`;
      if (icon){
        html += `<div class="mb-4 flex justify-center"><img src="${esc(icon)}" alt="" class="h-28 w-28 rounded-xl border border-slate-600 bg-slate-900 object-cover sm:h-36 sm:w-36" loading="lazy" onerror="this.style.display='none'" /></div>`;
      }
      if (prof){
        const rows = [
          ["俱乐部", prof.contestant_short_name || prof.contestant_name],
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
          html += `<div class="flex gap-2 border-b border-slate-800/80 pb-2 text-sm"><dt class="w-28 shrink-0 text-slate-500">${esc(k)}</dt><dd class="min-w-0 break-words text-slate-200">${esc(String(v))}</dd></div>`;
        }
        html += `</dl>`;
      } else {
        html += `<p class="text-sm leading-relaxed text-slate-400">未在 <code class="rounded bg-slate-800 px-1 text-xs">cfl_players_page_raw.json</code> 中匹配到该球员。部署流水线中运行 <code class="rounded bg-slate-800 px-1 text-xs">cfl_players_page_crawler.py</code> 后可显示位置、号码、头像等注册信息。</p>`;
      }
      if (playerProfileBodyEl) playerProfileBodyEl.innerHTML = html;
    }

    function renderMiniStatTable(container, rows, valueKey, valueLabel){
      if (!container) return;
      if (!rows.length){
        container.innerHTML = '<div class="rounded-lg border border-slate-700 bg-slate-900/50 p-3 text-sm text-slate-400">暂无数据</div>';
        return;
      }
      const head = `<thead class="sticky top-0 z-10 bg-slate-900/95 text-left text-[10px] uppercase tracking-wide text-slate-500 sm:text-xs">
        <tr><th class="px-1 py-1.5 sm:px-2">#</th><th class="px-1 py-1.5 sm:px-2">球员</th><th class="px-1 py-1.5 sm:px-2">球队</th><th class="px-1 py-1.5 text-right sm:px-2">${esc(valueLabel)}</th></tr></thead>`;
      const body = rows.map((p, i) => `
        <tr class="border-b border-slate-800/80 hover:bg-slate-800/40">
          <td class="px-1 py-1.5 font-mono text-slate-500 sm:px-2">${i + 1}</td>
          <td class="max-w-[7rem] truncate px-1 py-1.5 sm:max-w-none sm:px-2">
            <button type="button" class="mini-player-open-profile max-w-full truncate text-left text-[11px] font-medium text-accent underline-offset-2 hover:underline sm:text-sm">${esc(p.player_name)}</button>
          </td>
          <td class="max-w-[5rem] truncate px-1 py-1.5 text-slate-400 sm:max-w-none sm:px-2">${esc(p.team_name || "—")}</td>
          <td class="px-1 py-1.5 text-right font-mono font-semibold text-accent sm:px-2">${n(p[valueKey])}</td>
        </tr>`).join("");
      container.innerHTML = `<table class="w-full border-collapse text-[11px] sm:text-sm">${head}<tbody>${body}</tbody></table>`;
      container.querySelectorAll(".mini-player-open-profile").forEach((btn, i) => {
        btn.addEventListener("click", () => {
          const p = rows[i];
          if (p) showPlayerProfileSubview(p);
        });
      });
    }

    function renderGoalAssistRankings(players){
      const topN = 20;
      const byGoals = [...players].sort((a,b) => (b.goals - a.goals) || (b.assists - a.assists) || a.player_name.localeCompare(b.player_name)).slice(0, topN);
      const byAssists = [...players].sort((a,b) => (n(b.assists) - n(a.assists)) || (b.goals - a.goals) || a.player_name.localeCompare(b.player_name)).slice(0, topN);
      renderMiniStatTable(scorerRankListEl, byGoals, "goals", "进球");
      renderMiniStatTable(assistRankListEl, byAssists, "assists", "助攻");
    }

    function renderPlayers(){
      const league = getLeague();
      let players = buildPlayerStatsFromNormalized();
      if (!players.length){
        players = buildPlayerStats(league.matches);
      }
      renderGoalAssistRankings(players);
      playerListEl.innerHTML = players.map((p, idx) => `
        <div class="player-row mb-2 w-full rounded-lg border border-slate-700 bg-slate-900/60 p-2.5 sm:p-3">
          <div class="flex flex-col gap-0.5 min-[400px]:flex-row min-[400px]:items-center min-[400px]:justify-between">
            <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span class="font-mono text-xs text-slate-500">${idx + 1}.</span>
              <button type="button" class="player-name-open-profile text-left text-sm font-semibold text-accent underline-offset-2 hover:underline" data-player-index="${idx}">${esc(p.player_name)}</button>
            </div>
            <span class="text-[11px] text-slate-400 sm:text-xs">${esc(p.team_name || "—")}</span>
          </div>
          <div class="mt-1 text-[11px] text-slate-300 sm:text-xs">进球 ${p.goals} · 助攻 ${n(p.assists)} · 黄 ${p.yellow_card} · 红 ${p.red_card}</div>
        </div>
      `).join("");

      playerListEl.querySelectorAll(".player-name-open-profile").forEach((btn) => {
        btn.addEventListener("click", () => {
          const idx = Number(btn.dataset.playerIndex);
          showPlayerProfileSubview(players[idx]);
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
    if (backToPlayerListBtn) backToPlayerListBtn.addEventListener("click", showPlayersListSubview);
    clearClubFilterBtn.addEventListener("click", () => {
      selectedClub = "";
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

    async function bootDashboard(){
      let triedUrl = "";
      try {
        const embedUrl = resolveEmbedJsonUrl();
        embedUrl.searchParams.set("v", "__EMBED_V__");
        triedUrl = embedUrl.toString();
        const res = await fetch(triedUrl, { cache: "no-store" });
        if (!res.ok) throw new Error("dashboard_embed.json HTTP " + res.status);
        const bundle = await res.json();
        RAW_DATA = bundle.raw_data || { leagues: [] };
        RAW_PLAYER_STATS = Array.isArray(bundle.player_stats) ? bundle.player_stats : [];
        TEAM_LOGOS = bundle.team_logos && typeof bundle.team_logos === "object" ? bundle.team_logos : {};
        RAW_CFL_PROFILES = Array.isArray(bundle.cfl_player_profiles) ? bundle.cfl_player_profiles : [];
        renderAll();
      } catch (err) {
        console.error("bootDashboard failed, url=", triedUrl, err);
        const bar = document.createElement("div");
        bar.className = "fixed left-0 right-0 top-0 z-[100] bg-red-900 px-3 py-2 text-center text-sm text-white";
        bar.textContent = "数据加载失败（请检查是否已部署 dashboard_embed.json，或强制刷新 Ctrl+F5；控制台可见请求 URL）";
        document.body.insertBefore(bar, document.body.firstChild);
      }
    }
    bootDashboard();
  </script>
</body>
</html>
"""

    html = html.replace("__GENERATED__", generated_at)
    html = html.replace("__SOURCE_FILE__", source_file)
    html = html.replace("__EMBED_V__", embed_cache_bust)
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
    embed_cache_bust = str(int(datetime.now(timezone.utc).timestamp()))
    html = build_dashboard_html(source_file, generated_at, embed_cache_bust)

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
