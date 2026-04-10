import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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


def _extract_rows_from_json(payload: Any) -> List[Dict[str, Any]]:
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
            has_metric = any(
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
            if name and has_metric:
                rows.append(
                    {
                        "player_name": name,
                        "team_name": str(cur.get("teamName") or cur.get("team_name") or "").strip(),
                        "goals": _to_int(cur.get("goal") or cur.get("goals")),
                        "assists": _to_int(cur.get("assist") or cur.get("assists")),
                        "yellow_cards": _to_int(cur.get("yellow") or cur.get("yellow_cards")),
                        "red_cards": _to_int(cur.get("red") or cur.get("red_cards")),
                        "source_note": "leisu_har_import",
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


def parse_har(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = (raw.get("log") or {}).get("entries") or []
    all_rows: List[Dict[str, Any]] = []
    matched_urls: List[str] = []
    errors: List[str] = []

    for entry in entries:
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        url = str(request.get("url") or "")
        mime = str((response.get("content") or {}).get("mimeType") or "").lower()
        text = (response.get("content") or {}).get("text")
        if not text:
            continue
        is_candidate = (
            "leisu" in url.lower()
            and any(x in url.lower() for x in ("player", "shooter", "assist", "rank", "stat"))
        ) or "json" in mime
        if not is_candidate:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        rows = _extract_rows_from_json(payload)
        if rows:
            matched_urls.append(url)
            all_rows.extend(rows)

    if not all_rows:
        errors.append("no_player_rows_extracted_from_har")

    return {"rows": _dedup(all_rows), "matched_urls": matched_urls, "errors": errors}


def pick_latest_har(data_dir: Path) -> Optional[Path]:
    candidates = sorted(data_dir.glob("*.har"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Leisu player stats from browser HAR file")
    parser.add_argument("--har", required=False, help="Path to HAR file exported from browser devtools")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data"
    out_path = root / "data" / "csl_player_stats_leisu_raw.json"

    if args.har:
        har_path = Path(args.har).resolve()
    else:
        latest = pick_latest_har(data_dir)
        if latest is None:
            raise FileNotFoundError("No .har file found in data directory. Provide --har or place a HAR in data/.")
        har_path = latest

    result = parse_har(har_path)
    payload = {
        "meta": {
            "project": "China Football Season Monitor 2026",
            "league": "CSL",
            "dataset": "player_stats_supplement",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_url": result.get("matched_urls", []),
            "strategy": "har_import",
            "har_file": str(har_path),
            "errors": result.get("errors", []),
            "notes": [
                "Imported from browser HAR. This bypasses anti-bot pages that hide structured data.",
                "Use normalize_csl_data.py after import to merge into normalized dataset.",
            ],
        },
        "player_stats": result.get("rows", []),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"har import output written: {out_path}")
    print(f"rows: {len(payload['player_stats'])}")


if __name__ == "__main__":
    main()
