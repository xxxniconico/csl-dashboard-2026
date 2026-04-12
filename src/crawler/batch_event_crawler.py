import argparse
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


class BatchEventCrawler:
    WEB_BASE = "https://www.dongqiudi.com"
    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        index_path: Path,
        output_path: Path,
        checkpoint_path: Path,
        chunk_size: int = 10,
        timeout_s: int = 25,
        headless: bool = True,
        fast: bool = False,
        skip_complete: bool = False,
    ) -> None:
        self.index_path = index_path
        self.output_path = output_path
        self.checkpoint_path = checkpoint_path
        self.chunk_size = max(1, chunk_size)
        self.timeout_ms = timeout_s * 1000
        self.headless = headless
        self.fast = fast
        self.skip_complete = skip_complete

    def _read_json_array(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    def _write_json_array(self, path: Path, payload: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_checkpoint(self) -> Dict[str, Any]:
        if not self.checkpoint_path.exists():
            return {"last_successful_match_id": None, "updated_at_utc": None}
        with self.checkpoint_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save_checkpoint(self, match_id: str) -> None:
        payload = {
            "last_successful_match_id": match_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _human_delay(self, lo: float = 0.7, hi: float = 1.8) -> None:
        if self.fast:
            lo, hi = min(lo, 0.12), min(hi, 0.35)
        time.sleep(random.uniform(lo, hi))

    def _human_scroll(self, page: Any) -> None:
        if self.fast:
            return
        # Subtle random scrolls to mimic reading behavior.
        for _ in range(random.randint(1, 3)):
            delta = random.randint(180, 620)
            page.mouse.wheel(0, delta)
            self._human_delay(0.2, 0.6)
        page.mouse.wheel(0, -random.randint(80, 220))
        self._human_delay(0.2, 0.5)

    def _normalize_event(self, event_type: str, player_name: str, minute: Any) -> Dict[str, Any]:
        t = event_type.lower()
        if "goal" in t or "进球" in t:
            t = "goal"
        elif "yellow" in t or "黄牌" in t:
            t = "yellow_card"
        elif "red" in t or "红牌" in t:
            t = "red_card"
        else:
            return {}

        try:
            m = int(str(minute).strip())
        except Exception:
            m = None
        return {"type": t, "player_name": (player_name or "").strip(), "minute": m}

    def _extract_events(self, html: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []

        # Pattern 1: JSON-like events list inside scripts.
        script_json_hits = re.findall(r'"events"\s*:\s*(\[[\s\S]*?\])', html)
        for raw in script_json_hits:
            try:
                parsed = json.loads(raw)
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    evt = self._normalize_event(
                        str(item.get("type") or item.get("event_type") or ""),
                        str(item.get("player") or item.get("player_name") or item.get("name") or ""),
                        item.get("minute") or item.get("time"),
                    )
                    if evt:
                        events.append(evt)
            except Exception:
                continue

        # Pattern 2: key-value sequence in scripts/content.
        kv = re.findall(
            r'"type"\s*:\s*"([^"]+)".{0,220}?"minute"\s*:\s*"?(\d{1,3})"?'
            r'.{0,220}?"player[^"]*"\s*:\s*"([^"]+)"',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for evt_type, minute, player_name in kv:
            evt = self._normalize_event(evt_type, player_name, minute)
            if evt:
                events.append(evt)

        # Pattern 3: timeline plain text lines.
        text_hits = re.findall(
            r"(?:(\d{1,3})['′]?\s*)?(进球|黄牌|红牌|goal|yellow|red)\s*[:：]?\s*([^\n<]{1,40})",
            html,
            flags=re.IGNORECASE,
        )
        for minute, evt_type, player_name in text_hits:
            evt = self._normalize_event(evt_type, player_name, minute)
            if evt:
                events.append(evt)

        # De-duplicate.
        uniq = []
        seen = set()
        for e in events:
            key = (e["type"], e["player_name"], e["minute"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(e)
        return sorted(uniq, key=lambda x: x.get("minute") if x.get("minute") is not None else 999)

    def _wait_after_navigation(self, page: Any) -> None:
        """networkidle is very slow on SPAs; load + short sleep is enough for event HTML."""
        if self.fast:
            try:
                page.wait_for_load_state("load", timeout=min(12000, self.timeout_ms))
            except Exception:
                pass
            time.sleep(0.2)
            return
        try:
            page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        except Exception:
            pass

    def _crawl_one_match(self, page: Any, match: Dict[str, Any]) -> Dict[str, Any]:
        match_id = str(match.get("match_id", ""))
        detail_path = str(match.get("detail_path", ""))
        errors: List[str] = []

        if not match_id:
            return {
                **match,
                "events": [],
                "errors": ["missing_match_id"],
                "event_scraped_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        if not detail_path:
            return {
                **match,
                "events": [],
                "errors": ["missing_detail_path"],
                "event_scraped_at_utc": datetime.now(timezone.utc).isoformat(),
            }

        candidate_urls = [
            f"{self.WEB_BASE}{detail_path}",
            f"http://api.dongqiudi.com{detail_path}",
        ]
        seen = set()
        candidate_urls = [u for u in candidate_urls if not (u in seen or seen.add(u))]

        best_events: List[Dict[str, Any]] = []
        for url in candidate_urls:
            for attempt in range(1, 2 + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                    self._human_delay(0.8, 1.6)
                    self._human_scroll(page)
                    self._wait_after_navigation(page)

                    if "/live" in page.url:
                        errors.append(f"redirected_to_live_probable_antibot@{url}")
                        break

                    html = page.content()
                    events = self._extract_events(html)
                    if events:
                        best_events = events
                        break
                    errors.append(f"no_events_found@{url}#try{attempt}")
                except PlaywrightTimeoutError as exc:
                    errors.append(f"timeout@{url}#try{attempt}: {exc}")
                except Exception as exc:
                    errors.append(f"exception@{url}#try{attempt}: {exc}")
                self._human_delay(1.0 if not self.fast else 0.2, 2.0 if not self.fast else 0.45)
            if best_events:
                break

        return {
            **match,
            "events": best_events,
            "errors": errors,
            "event_scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    def run(self, max_matches: int = 0) -> None:
        index_matches = self._read_json_array(self.index_path)
        existing = self._read_json_array(self.output_path)
        existing_by_id = {str(m.get("match_id", "")): m for m in existing if m.get("match_id")}
        checkpoint = self._load_checkpoint()
        last_ok = checkpoint.get("last_successful_match_id")

        # resume offset
        start = 0
        if last_ok:
            for i, m in enumerate(index_matches):
                if str(m.get("match_id")) == str(last_ok):
                    start = i + 1
                    break

        pending = index_matches[start:]
        if max_matches and max_matches > 0:
            pending = pending[:max_matches]

        print(f"index total: {len(index_matches)}, resume from: {start}, pending: {len(pending)}")
        if not pending:
            print("nothing to process")
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=self.DEFAULT_UA,
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            # Reduce heavy static resources to improve stability and lower bot-like burstiness.
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "font", "media"}
                else route.continue_(),
            )
            page = context.new_page()
            page.set_extra_http_headers({"Referer": "https://www.dongqiudi.com/"})

            for i in range(0, len(pending), self.chunk_size):
                chunk = pending[i : i + self.chunk_size]
                print(f"processing chunk {i // self.chunk_size + 1}, size={len(chunk)}")
                for match in chunk:
                    match_id = str(match.get("match_id", ""))
                    if self.skip_complete and match_id and match_id in existing_by_id:
                        prev = existing_by_id[match_id]
                        ev = prev.get("events") or []
                        if isinstance(ev, list) and len(ev) > 0:
                            self._save_checkpoint(match_id)
                            continue
                    enriched = self._crawl_one_match(page, match)
                    match_id = str(enriched.get("match_id", ""))
                    if match_id:
                        existing_by_id[match_id] = enriched
                        self._save_checkpoint(match_id)
                    self._human_delay(0.9, 2.2)

                # persist after each chunk (idempotent refresh behavior)
                output_rows = sorted(existing_by_id.values(), key=lambda x: str(x.get("match_id", "")))
                self._write_json_array(self.output_path, output_rows)
                print(f"checkpoint persisted, output size={len(output_rows)}")

            context.close()
            browser.close()

        print(f"done. output: {self.output_path}")


def filter_matches(matches: List[Dict[str, Any]], finished_only: bool, year: int) -> List[Dict[str, Any]]:
    out = []
    for m in matches:
        if finished_only and str(m.get("status", "")).lower() != "finished":
            continue
        date_text = str(m.get("date") or "")
        # Accept date prefix style like "2026-03-06 19:35".
        if year > 0 and not date_text.startswith(f"{year}-"):
            continue
        out.append(m)
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Batch scrape CSL match events from index")
    parser.add_argument(
        "--index",
        default=str(root / "data" / "all_matches_2026_index.json"),
        help="Path to master schedule index JSON",
    )
    parser.add_argument(
        "--output",
        default=str(root / "data" / "csl_matches_enriched.json"),
        help="Path to enriched output JSON",
    )
    parser.add_argument(
        "--checkpoint",
        default=str(root / "data" / "batch_event_checkpoint.json"),
        help="Path to checkpoint JSON",
    )
    parser.add_argument("--chunk-size", type=int, default=10, help="Matches processed per chunk")
    parser.add_argument("--timeout", type=int, default=25, help="Per-page timeout in seconds")
    parser.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Shorter waits and load instead of networkidle (recommended in CI)",
    )
    parser.add_argument(
        "--skip-complete",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Skip matches that already have events in output JSON (incremental)",
    )
    parser.add_argument("--max-matches", type=int, default=0, help="Limit processed matches for testing")
    parser.add_argument("--finished-only", action="store_true", help="Process only finished matches")
    parser.add_argument("--year", type=int, default=0, help="Filter by match date year, e.g. 2026")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Ignore and recreate checkpoint from start")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser")
    args = parser.parse_args()

    # Optional pre-filter: write a temporary filtered index for this run only.
    root = Path(__file__).resolve().parents[2]
    raw_index_path = Path(args.index)
    if args.finished_only or args.year:
        source_rows = []
        if raw_index_path.exists():
            with raw_index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    source_rows = data
        filtered_rows = filter_matches(source_rows, finished_only=args.finished_only, year=args.year)
        temp_index_path = root / "data" / "_temp_filtered_index.json"
        temp_index_path.write_text(json.dumps(filtered_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        index_path = temp_index_path
        print(f"filtered index size: {len(filtered_rows)}")
    else:
        index_path = raw_index_path

    in_ci = os.environ.get("CI", "").lower() in ("true", "1", "yes")
    use_fast = args.fast if args.fast is not None else in_ci
    use_skip = args.skip_complete if args.skip_complete is not None else in_ci

    crawler = BatchEventCrawler(
        index_path=index_path,
        output_path=Path(args.output),
        checkpoint_path=Path(args.checkpoint),
        chunk_size=args.chunk_size,
        timeout_s=args.timeout,
        headless=not args.headed,
        fast=use_fast,
        skip_complete=use_skip,
    )
    if args.reset_checkpoint:
        cp = Path(args.checkpoint)
        if cp.exists():
            cp.unlink()
        print("checkpoint reset")
    crawler.run(max_matches=args.max_matches)


if __name__ == "__main__":
    main()
