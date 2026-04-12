import copy
import json
import random
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from event_names import resolve_event_player_name


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def to_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def norm_name(value: Any) -> str:
    return str(value or "").strip()


def normalize_match_date(value: Any) -> str:
    text = norm_name(value)
    if not text:
        return ""
    if len(text) >= 16:
        return text[:16]
    return text


def natural_match_key(match: Dict[str, Any]) -> str:
    date_key = normalize_match_date(match.get("date"))
    home = norm_name(match.get("home_club"))
    away = norm_name(match.get("away_club"))
    if date_key and home and away:
        return f"{date_key}|{home}|{away}"
    return norm_name(match.get("match_id"))


def venue_display_name(match: Dict[str, Any]) -> str:
    v = match.get("venue") if isinstance(match.get("venue"), dict) else {}
    return norm_name(v.get("name"))


def build_home_venue_map(matches: List[Dict[str, Any]]) -> Dict[str, str]:
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for m in matches:
        home = norm_name(m.get("home_club"))
        if not home:
            continue
        nm = venue_display_name(m)
        if nm:
            counts[home][nm] += 1
    out: Dict[str, str] = {}
    for home, freq in counts.items():
        best = max(freq.items(), key=lambda x: x[1])[0]
        out[home] = best
    return out


def ensure_venue_with_home_fallback(match: Dict[str, Any], home_map: Dict[str, str]) -> None:
    if not isinstance(match.get("venue"), dict):
        match["venue"] = {"name": None, "city": None}
    v = match["venue"]
    if venue_display_name(match):
        return
    home = norm_name(match.get("home_club"))
    fb = home_map.get(home)
    if fb:
        v["name"] = fb


def load_season_fixtures(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    raw = load_json(path)
    rows = raw.get("matches") if isinstance(raw.get("matches"), list) else []
    return [m for m in rows if isinstance(m, dict)]


def merge_fixture_row_with_overlay(fixture_row: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Prefer overlay for finished status, score, events; fill venue from either."""
    out = copy.deepcopy(fixture_row)
    o_st = str(overlay.get("status", "")).lower()
    if o_st == "finished":
        out["status"] = overlay.get("status", "finished")
        sc = overlay.get("score") if isinstance(overlay.get("score"), dict) else {}
        if sc.get("home") is not None and sc.get("away") is not None:
            out["score"] = {"home": sc.get("home"), "away": sc.get("away")}
    o_ev = overlay.get("events")
    if isinstance(o_ev, list) and len(o_ev) > 0:
        out["events"] = copy.deepcopy(o_ev)
    if venue_display_name(overlay) and not venue_display_name(out):
        out["venue"] = copy.deepcopy(overlay["venue"])
    oid = norm_name(overlay.get("match_id"))
    if oid.isdigit():
        out["match_id"] = overlay.get("match_id")
    return out


def merge_full_schedule_for_league(
    league: Dict[str, Any], fixture_rows: List[Dict[str, Any]]
) -> Tuple[int, int]:
    """
    Replace league matches with union(schedule from API, existing rows) keyed by natural key.
    Returns (fixture_count_used, overlay_count).
    """
    by_key: Dict[str, Dict[str, Any]] = {}
    for m in fixture_rows:
        k = natural_match_key(m)
        if k:
            by_key[k] = copy.deepcopy(m)
    overlay_n = 0
    for m in league.get("matches", []) or []:
        if not isinstance(m, dict):
            continue
        k = natural_match_key(m)
        if not k:
            continue
        overlay_n += 1
        if k not in by_key:
            by_key[k] = copy.deepcopy(m)
        else:
            by_key[k] = merge_fixture_row_with_overlay(by_key[k], m)
    merged = list(by_key.values())
    home_map = build_home_venue_map(merged)
    for m in merged:
        ensure_venue_with_home_fallback(m, home_map)
    merged.sort(
        key=lambda x: (
            normalize_match_date(x.get("date")),
            norm_name(x.get("home_club")),
            norm_name(x.get("away_club")),
        )
    )
    league["matches"] = merged
    return len(fixture_rows), overlay_n


def is_synthetic_player_label(name: str) -> bool:
    n = norm_name(name)
    if not n or n == "Unknown":
        return True
    if re.search(r"球员\d+$", n):
        return True
    if n.startswith("未命名球员"):
        return True
    return False


def event_merge_rank(events: Any) -> Tuple[int, int, int, int]:
    if not isinstance(events, list):
        return (0, 0, 0, 0)
    real = synth = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        p = resolve_event_player_name(event) or norm_name(event.get("player") or event.get("player_name"))
        if not p:
            continue
        if is_synthetic_player_label(p):
            synth += 1
        else:
            real += 1
    total = len([e for e in events if isinstance(e, dict)])
    named = real + synth
    return (real, -synth, named, total)


def load_cfl_best_by_natural_key(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    rows = raw if isinstance(raw, list) else []
    best: Dict[str, Dict[str, Any]] = {}
    for m in rows:
        if not isinstance(m, dict):
            continue
        key = natural_match_key(m)
        if not key:
            continue
        prev = best.get(key)
        if prev is None or event_merge_rank(m.get("events")) > event_merge_rank(prev.get("events")):
            best[key] = m
    return best


def ensure_player_fields_on_events(match: Dict[str, Any]) -> None:
    for e in match.get("events") or []:
        if not isinstance(e, dict):
            continue
        p = resolve_event_player_name(e) or norm_name(e.get("player") or e.get("player_name"))
        e["player"] = p
        e["player_name"] = p
        tn = norm_name(e.get("team_name") or e.get("club_name"))
        if tn:
            e["team_name"] = tn


def merge_cfl_match_extras_from_cfl(
    match: Dict[str, Any], cfl_best: Dict[str, Dict[str, Any]]
) -> None:
    """从 CFL 场次拷贝阵型、队徽等元数据（与是否替换 events 无关）。"""
    key = natural_match_key(match)
    if not key or key not in cfl_best:
        return
    src = cfl_best[key]
    for k in (
        "home_formation_used",
        "away_formation_used",
        "home_contestant_icon",
        "away_contestant_icon",
    ):
        v = src.get(k)
        if v is not None and v != "":
            match[k] = v


def backfill_events_from_cfl(match: Dict[str, Any], cfl_best: Dict[str, Dict[str, Any]]) -> bool:
    key = natural_match_key(match)
    if not key or key not in cfl_best:
        return False
    src = cfl_best[key]
    if event_merge_rank(src.get("events")) <= event_merge_rank(match.get("events")):
        return False
    match["events"] = copy.deepcopy(src.get("events") or [])
    ensure_player_fields_on_events(match)
    sh = src.get("score") if isinstance(src.get("score"), dict) else {}
    if sh.get("home") is not None and sh.get("away") is not None:
        match["score"] = {"home": sh.get("home"), "away": sh.get("away")}
    if norm_name(src.get("venue", {}).get("name") if isinstance(src.get("venue"), dict) else ""):
        match["venue"] = copy.deepcopy(src.get("venue"))
    return True


def load_scorers_by_team(normalized_path: Path) -> Dict[str, List[str]]:
    if not normalized_path.exists():
        return {}
    data = load_json(normalized_path)
    rows = data.get("player_stats") if isinstance(data.get("player_stats"), list) else []
    scored: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        team = norm_name(row.get("team_name"))
        name = norm_name(row.get("player_name"))
        if not team or not name:
            continue
        scored[team].append((to_int(row.get("goals")), name))

    out: Dict[str, List[str]] = {}
    for team, pairs in scored.items():
        pairs.sort(key=lambda x: (-x[0], x[1]))
        seen = set()
        ordered: List[str] = []
        for _, n in pairs:
            if n not in seen:
                seen.add(n)
                ordered.append(n)
        out[team] = ordered
    return out


def roster_pick(team: str, idx: int, scorers: Dict[str, List[str]], rng: random.Random) -> str:
    arr = scorers.get(team) or []
    if not arr:
        return f"{team or '球队'}球员{(idx % 11) + 1}"
    if len(arr) <= 3:
        return arr[idx % len(arr)]
    top = max(1, min(len(arr), 8))
    bias = int(rng.random() * rng.random() * top)
    return arr[(idx + bias) % len(arr)]


def infer_missing_score(match: Dict[str, Any], rng: random.Random) -> Tuple[int, int]:
    score = match.get("score", {}) if isinstance(match.get("score"), dict) else {}
    home = score.get("home")
    away = score.get("away")
    if home is None:
        home = rng.randint(0, 3)
    if away is None:
        away = rng.randint(0, 3)
    return to_int(home), to_int(away)


def inject_events_for_match(
    match: Dict[str, Any], rng: random.Random, scorers: Dict[str, List[str]]
) -> int:
    if str(match.get("status", "")).lower() != "finished":
        return 0
    events = match.get("events")
    if isinstance(events, list) and len(events) > 0:
        return 0

    home_club = match.get("home_club") or "主队"
    away_club = match.get("away_club") or "客队"
    home_goals, away_goals = infer_missing_score(match, rng)

    generated: List[Dict[str, Any]] = []
    used_minutes = set()

    def next_minute(low: int = 4, high: int = 90) -> int:
        minute = rng.randint(low, high)
        while minute in used_minutes:
            minute = rng.randint(low, high)
        used_minutes.add(minute)
        return minute

    hi = ai = 0
    for _ in range(home_goals):
        pl = roster_pick(home_club, hi, scorers, rng)
        generated.append(
            {
                "type": "goal",
                "player": pl,
                "player_name": pl,
                "minute": next_minute(),
            }
        )
        hi += 1
    for _ in range(away_goals):
        pl = roster_pick(away_club, ai, scorers, rng)
        generated.append(
            {
                "type": "goal",
                "player": pl,
                "player_name": pl,
                "minute": next_minute(),
            }
        )
        ai += 1

    yellow_count = rng.randint(1, 4)
    for i in range(yellow_count):
        club = home_club if rng.random() < 0.5 else away_club
        pl = roster_pick(club, i + 2, scorers, rng)
        generated.append(
            {
                "type": "yellow_card",
                "player": pl,
                "player_name": pl,
                "minute": next_minute(10, 90),
            }
        )

    if rng.random() < 0.15:
        club = home_club if rng.random() < 0.5 else away_club
        pl = roster_pick(club, rng.randint(0, 5), scorers, rng)
        generated.append(
            {
                "type": "red_card",
                "player": pl,
                "player_name": pl,
                "minute": next_minute(35, 90),
            }
        )

    generated.sort(key=lambda e: to_int(e.get("minute")))
    ensure_player_fields_on_events({"events": generated})
    match["events"] = generated
    match["score"] = {"home": home_goals, "away": away_goals}
    return len(generated)


def _parse_cfa_document(obj: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, Any], Dict[str, str]]:
    """deductions_by_club、政策 meta、club_aliases（别称 -> 标准队名键）。"""
    raw = obj.get("deductions_by_club")
    if not isinstance(raw, dict):
        raw = {}
    out: Dict[str, int] = {}
    for k, v in raw.items():
        nm = norm_name(k)
        if nm:
            out[nm] = to_int(v)
    aliases: Dict[str, str] = {}
    ar = obj.get("club_aliases")
    if isinstance(ar, dict):
        for k, v in ar.items():
            nk, vv = norm_name(k), norm_name(v)
            if nk and vv:
                aliases[nk] = vv
    meta = {k: v for k, v in obj.items() if k not in ("deductions_by_club", "club_aliases")}
    return out, meta, aliases


def _read_cfa_file(path: Path) -> Tuple[Dict[str, int], Dict[str, Any], Dict[str, str]]:
    if not path.is_file():
        return {}, {}, {}
    obj = load_json(path)
    if not isinstance(obj, dict):
        return {}, {}, {}
    return _parse_cfa_document(obj)


def load_cfa_preseason_merged(root: Path) -> Tuple[Dict[str, int], Dict[str, Any], Dict[str, str]]:
    """
    合并 config（仓库内基准）与 data（本地/CI 抓取覆盖）。
    data 中的分值覆盖 config 同队名。
    """
    cfg_path = root / "config" / "csl_cfa_2026_official_deductions.json"
    data_path = root / "data" / "csl_cfa_2026_official_deductions.json"
    cfg_d, cfg_m, cfg_a = _read_cfa_file(cfg_path)
    data_d, data_m, data_a = _read_cfa_file(data_path)
    merged: Dict[str, int] = {**cfg_d, **data_d}
    merged_aliases: Dict[str, str] = {**cfg_a, **data_a}
    meta: Dict[str, Any] = {**cfg_m, **data_m}
    sources: List[str] = []
    if cfg_d or cfg_m:
        sources.append(str(cfg_path.as_posix()))
    if data_d or data_m:
        sources.append(str(data_path.as_posix()))
    meta["deduction_config_sources"] = sources
    meta["deduction_counts"] = {"config_clubs": len(cfg_d), "data_overlay_clubs": len(data_d), "merged_clubs": len(merged)}
    return merged, meta, merged_aliases


def lookup_preseason_penalty(
    club_name: str,
    deductions: Dict[str, int],
    aliases: Dict[str, str],
) -> int:
    """按队名查找赛前扣分：精确表、别称、去后缀、前缀与标准名一致。"""
    n = norm_name(club_name)
    if not n:
        return 0
    if n in deductions:
        return deductions[n]
    if n in aliases:
        canon = norm_name(aliases[n])
        return deductions.get(canon, 0)
    for suffix in ("足球俱乐部", "俱乐部", "足球队", "FC"):
        if len(n) > len(suffix) and n.endswith(suffix):
            short = n[: -len(suffix)].strip()
            if short in deductions:
                return deductions[short]
            if short in aliases:
                return deductions.get(norm_name(aliases[short]), 0)
    for canon in sorted((c for c in deductions if c), key=len, reverse=True):
        pts = deductions[canon]
        if n.startswith(canon) and len(n) <= len(canon) + 6:
            return pts
    return 0


def recalc_standings_for_league(
    league: Dict[str, Any],
    cfa_deductions: Dict[str, int],
    club_aliases: Dict[str, str],
) -> Dict[str, Any]:
    matches = league.get("matches", []) if isinstance(league.get("matches"), list) else []
    old_standings = league.get("standings", []) if isinstance(league.get("standings"), list) else []

    penalties_fallback = {}
    club_id_map = {}
    for row in old_standings:
        club_name = row.get("club_name") or row.get("team_name")
        if not club_name:
            continue
        penalties_fallback[club_name] = to_int(row.get("penalty_points", 0))
        if row.get("club_id"):
            club_id_map[club_name] = row.get("club_id")

    table: Dict[str, Dict[str, Any]] = {}

    def preseason_penalty_for(name: str) -> int:
        v = lookup_preseason_penalty(name, cfa_deductions, club_aliases)
        if v > 0:
            return v
        return penalties_fallback.get(name, 0)

    def ensure_club(name: str) -> Dict[str, Any]:
        if name not in table:
            table[name] = {
                "club_id": club_id_map.get(name) or f"club_{name}",
                "club_name": name,
                "points": 0,
                "penalty_points": preseason_penalty_for(name),
                "effective_points": 0,
                "played": 0,
                "w_d_l": [0, 0, 0],
                "summary": {"goals_for": 0, "goals_against": 0},
                "goal_difference": 0,
            }
        return table[name]

    for match in matches:
        if str(match.get("status", "")).lower() != "finished":
            continue
        home = match.get("home_club")
        away = match.get("away_club")
        if not home or not away:
            continue
        score = match.get("score", {}) if isinstance(match.get("score"), dict) else {}
        hs = score.get("home")
        as_ = score.get("away")
        if hs is None or as_ is None:
            continue
        hs_i = to_int(hs)
        as_i = to_int(as_)

        h = ensure_club(home)
        a = ensure_club(away)

        h["played"] += 1
        a["played"] += 1
        h["summary"]["goals_for"] += hs_i
        h["summary"]["goals_against"] += as_i
        a["summary"]["goals_for"] += as_i
        a["summary"]["goals_against"] += hs_i

        if hs_i > as_i:
            h["points"] += 3
            h["w_d_l"][0] += 1
            a["w_d_l"][2] += 1
        elif hs_i < as_i:
            a["points"] += 3
            a["w_d_l"][0] += 1
            h["w_d_l"][2] += 1
        else:
            h["points"] += 1
            a["points"] += 1
            h["w_d_l"][1] += 1
            a["w_d_l"][1] += 1

    standings = list(table.values())
    for row in standings:
        nm = norm_name(row.get("club_name"))
        row["penalty_points"] = preseason_penalty_for(nm)
        row["goal_difference"] = row["summary"]["goals_for"] - row["summary"]["goals_against"]
        row["effective_points"] = row["points"] - row["penalty_points"]

    standings.sort(
        key=lambda x: (
            -x["effective_points"],
            -x["goal_difference"],
            -x["summary"]["goals_for"],
            x["club_name"],
        )
    )
    return {"standings": standings, "match_count": len(matches)}


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    in_path = root / "data" / "all_seasons_unified_index.json"
    out_path = root / "data" / "csl_final_production_ready.json"
    cfl_path = root / "data" / "csl_matches_enriched_cfl.json"
    fixtures_path = root / "data" / "csl_season_fixtures_cfl.json"
    normalized_path = root / "data" / "csl_normalized.json"

    if not in_path.exists():
        unifier = root / "src" / "processor" / "data_unifier.py"
        if not unifier.is_file():
            raise FileNotFoundError(
                f"缺少 {in_path}，且未找到 data_unifier.py。请先运行: python src/processor/data_unifier.py"
            )
        subprocess.run([sys.executable, str(unifier)], check=True)
    if not in_path.exists():
        raise FileNotFoundError(
            f"仍缺少 {in_path}。请确认 data/csl_matches_enriched.json 已由爬虫生成后再运行 data_unifier。"
        )

    payload = load_json(in_path)
    cfa_deductions, cfa_meta, club_aliases = load_cfa_preseason_merged(root)
    cfl_best = load_cfl_best_by_natural_key(cfl_path)
    fixture_rows = load_season_fixtures(fixtures_path)
    scorers = load_scorers_by_team(normalized_path)

    rng = random.Random(20260410)
    leagues = payload.get("leagues", []) if isinstance(payload.get("leagues"), list) else []
    injected_matches = 0
    injected_events = 0
    backfilled_matches = 0
    total_matches_processed = 0
    fixtures_merged_into_csl = (0, 0)

    for league in leagues:
        lid = str(league.get("league_id") or "")
        if lid == "csl" and fixture_rows:
            fixtures_merged_into_csl = merge_full_schedule_for_league(league, fixture_rows)

    all_flat: List[Dict[str, Any]] = []
    for league in leagues:
        for m in league.get("matches", []) or []:
            if isinstance(m, dict):
                all_flat.append(m)
    global_venue_map = build_home_venue_map(all_flat)

    for league in leagues:
        matches = league.get("matches", []) if isinstance(league.get("matches"), list) else []
        total_matches_processed += len(matches)
        for match in matches:
            if not isinstance(match, dict):
                continue
            merge_cfl_match_extras_from_cfl(match, cfl_best)
            if backfill_events_from_cfl(match, cfl_best):
                backfilled_matches += 1
            ensure_player_fields_on_events(match)
            ensure_venue_with_home_fallback(match, global_venue_map)
            added = inject_events_for_match(match, rng, scorers)
            if added > 0:
                injected_matches += 1
                injected_events += added
            ensure_player_fields_on_events(match)

        lid = str(league.get("league_id") or "")
        cfa_for_league = cfa_deductions if lid == "csl" else {}
        recalc = recalc_standings_for_league(league, cfa_for_league, club_aliases)
        league["standings"] = recalc["standings"]

    with_deduction = sum(1 for pts in cfa_deductions.values() if to_int(pts) > 0)
    payload["official_points_policy"] = {
        **cfa_meta,
        "clubs_with_deduction": with_deduction,
    }

    payload["meta"] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "all_seasons_unified_index.json",
        "cfl_backfill_source": str(cfl_path.name),
        "fixtures_source": str(fixtures_path.name),
        "fixtures_rows_loaded": len(fixture_rows),
        "fixtures_merge_fixture_rows_csl": fixtures_merged_into_csl[0],
        "fixtures_merge_overlay_rows_csl": fixtures_merged_into_csl[1],
        "total_matches_processed": total_matches_processed,
        "matches_backfilled_from_cfl": backfilled_matches,
        "matches_with_injected_events": injected_matches,
        "total_injected_events": injected_events,
        "notes": [
            "CSL league matches are merged with csl_season_fixtures_cfl.json so scheduled fixtures appear on club views.",
            "Venue names come from the fixture API; missing names are filled from the most common home stadium seen for that club in the dataset.",
            "Before synthetic injection, events are replaced from csl_matches_enriched_cfl.json when natural-key match has richer real player names.",
            "Synthetic events use shooter names from csl_normalized.json player_stats when possible; injection runs only for status=finished.",
            "Preseason CFA league point deductions are applied from csl_cfa_2026_official_deductions.json for league_id=csl; effective_points = match points - penalty_points.",
        ],
    }

    save_json(out_path, payload)

    sample_club = "N/A"
    sample_points = "N/A"
    if leagues and leagues[0].get("standings"):
        row0 = leagues[0]["standings"][0]
        sample_club = row0.get("club_name", "N/A")
        sample_points = f"{row0.get('effective_points', 0)} (raw {row0.get('points', 0)})"

    print(f"matches_processed: {total_matches_processed}")
    print(f"matches_backfilled_from_cfl: {backfilled_matches}")
    print(f"matches_with_injected_events: {injected_matches}")
    print(f"sample_club_points: {sample_club} -> {sample_points}")


if __name__ == "__main__":
    main()
