"""
CSL 赛程索引：从雷速体育页面（Playwright + XHR JSON）生成 all_matches_2026_index.json。
不再使用懂球帝赛程接口。
"""
import json
import sys
from pathlib import Path
from typing import Dict

_CRAWLER_DIR = Path(__file__).resolve().parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from leisu_schedule import crawl_leisu_csl_index  # noqa: E402


def crawl_full_2026_schedule() -> Dict[str, object]:
    """与历史函数名兼容，供 data 管道调用。"""
    return crawl_leisu_csl_index()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    output_path = root / "data" / "all_matches_2026_index.json"
    result = crawl_full_2026_schedule()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result["matches"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {output_path}")
    meta = result.get("meta") or {}
    print(f"matches: {meta.get('match_count')}, seeds tried: {len(meta.get('seed_urls') or [])}")
    if meta.get("page_errors"):
        print(f"page errors: {len(meta['page_errors'])}")
        for item in meta["page_errors"][:5]:
            print(f" - {item.get('url')} :: {item.get('error')}")


if __name__ == "__main__":
    main()
