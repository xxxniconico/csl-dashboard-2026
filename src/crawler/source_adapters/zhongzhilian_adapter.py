import re
from typing import List

import requests
from bs4 import BeautifulSoup


DISCIPLINARY_LIST_URL = "https://www.cfl-china.cn/zh/newsannouncements/disciplinarydecisionslist.html"


def fetch_disciplinary_events(session: requests.Session) -> dict:
    """
    Best-effort fetch from official disciplinary list page.
    Some environments receive dynamic shells without article list data.
    """
    response = session.get(DISCIPLINARY_LIST_URL, timeout=20)
    response.raise_for_status()
    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    errors = []

    # Try common list anchors first.
    candidates = []
    for a in soup.select("a[href]"):
        title = a.get_text(" ", strip=True)
        href = a.get("href", "")
        if not title:
            continue
        if any(k in title for k in ("处罚", "纪律", "红牌", "黄牌")):
            candidates.append({"title": title, "href": href})

    if not candidates:
        errors.append("disciplinary page accessible but no structured item links found in HTML")

    # Very lightweight extraction from title only.
    extracted: List[dict] = []
    for item in candidates:
        title = item["title"]
        # Names in Chinese football bulletins are usually 2-4 Chinese chars.
        name_match = re.search(r"([\u4e00-\u9fff]{2,4})", title)
        player_name = name_match.group(1) if name_match else ""
        yellow_cards = 1 if "黄牌" in title else None
        red_cards = 1 if ("红牌" in title or "两黄变一红" in title) else None
        extracted.append(
            {
                "player_name": player_name,
                "team_name": "",
                "yellow_cards": yellow_cards,
                "red_cards": red_cards,
                "evidence_title": title,
                "source_url": item["href"],
            }
        )

    # Keep only entries that indicate card events.
    extracted = [x for x in extracted if x["yellow_cards"] or x["red_cards"]]
    if not extracted:
        errors.append("no yellow/red card disciplinary events extracted from current page HTML")

    return {"events": extracted, "errors": errors, "source_url": DISCIPLINARY_LIST_URL}
