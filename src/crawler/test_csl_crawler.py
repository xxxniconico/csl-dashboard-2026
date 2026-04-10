import json

from csl_crawler import CSLCrawler


def main() -> None:
    crawler = CSLCrawler()
    matches = crawler.crawl()
    print(f"total matches: {len(matches)}")
    print("sample:")
    print(json.dumps(matches[:2], ensure_ascii=False, indent=2))

    finished = sum(1 for m in matches if m.get("status") == "finished")
    with_events = sum(1 for m in matches if m.get("events"))
    print(f"finished matches: {finished}")
    print(f"matches with extracted events: {with_events}")


if __name__ == "__main__":
    main()
