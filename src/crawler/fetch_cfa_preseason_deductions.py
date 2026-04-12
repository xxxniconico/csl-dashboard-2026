"""
尝试从公开报道抓取/校验「赛季初联赛积分扣分」，写入 data/csl_cfa_2026_official_deductions.json，
与 config/ 内基准合并后由 data_enricher 使用。

失败时不抛错（由 CI continue-on-error 或本地忽略），仍以 config 为准。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src" / "processor") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "processor"))


def _load_config() -> Dict[str, Any]:
    p = ROOT / "config" / "csl_cfa_2026_official_deductions.json"
    if not p.is_file():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _fetch_text(url: str, timeout: int = 25) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_deductions_near_clubs(text: str, club_names: List[str]) -> Dict[str, int]:
    """在俱乐部名称附近窗口内寻找「扣N分」或「N分」。"""
    out: Dict[str, int] = {}
    for club in sorted(club_names, key=len, reverse=True):
        idx = text.find(club)
        if idx < 0:
            continue
        window = text[max(0, idx - 40) : idx + 140]
        m = re.search(r"扣(?:除|罚)?\s*(\d{1,2})\s*分", window)
        if m:
            out[club] = int(m.group(1))
            continue
        m2 = re.search(r"(\d{1,2})\s*分", window)
        if m2:
            n = int(m2.group(1))
            if 1 <= n <= 30:
                out[club] = n
    return out


def merge_and_write(
    baseline: Dict[str, Any],
    scraped: Dict[str, int],
) -> Tuple[Path, Dict[str, Any]]:
    base_d = baseline.get("deductions_by_club")
    if not isinstance(base_d, dict):
        base_d = {}
    # 基准 config 覆盖同队名抓取结果，避免报道中「10 支球队」等噪声被误记为扣 10 分
    base_norm = {k: int(v) for k, v in base_d.items() if str(k).strip()}
    merged_clubs = {**scraped, **base_norm}
    out_obj = dict(baseline)
    out_obj["deductions_by_club"] = merged_clubs
    out_obj["meta_scrape"] = {
        "scraped_keys": list(scraped.keys()),
        "merged_total": len(merged_clubs),
    }
    out_path = ROOT / "data" / "csl_cfa_2026_official_deductions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path, out_obj


def main() -> None:
    cfg = _load_config()
    if not cfg:
        print("skip: no config/csl_cfa_2026_official_deductions.json")
        return

    refs = cfg.get("references")
    urls: List[str] = []
    if isinstance(refs, list):
        for r in refs:
            if isinstance(r, dict) and r.get("url"):
                u = str(r["url"]).strip()
                if u.startswith("http"):
                    urls.append(u)
    # 中文稿优先（队名与「扣N分」表述更一致）
    urls.sort(key=lambda u: 0 if "sina" in u or "163" in u or "qq" in u else 1)

    clubs = list(cfg.get("deductions_by_club", {}).keys()) if isinstance(cfg.get("deductions_by_club"), dict) else []
    if not clubs or not urls:
        print("skip: no clubs or urls in config")
        return

    scraped: Dict[str, int] = {}
    for url in urls[:2]:
        try:
            html = _fetch_text(url)
            text = _strip_html(html)
            part = extract_deductions_near_clubs(text, clubs)
            scraped.update(part)
            print(f"fetched {url} -> matched {len(part)} clubs")
        except Exception as exc:
            print(f"warn: {url} -> {exc}")

    if not scraped:
        print("no scraped deductions; data overlay not written")
        return

    path, _ = merge_and_write(cfg, scraped)
    print(f"wrote {path} (merged {len(scraped)} scraped keys)")


if __name__ == "__main__":
    main()
