"""Ashby adapter.

Uses the public, unauthenticated posting API:
https://api.ashbyhq.com/posting-api/job-board/{company}

Do NOT scrape jobs.ashbyhq.com/{company} - it is a JS app that renders
nothing without a browser. The posting API is the correct door.
"""

import html
import re

from fetchers.ashby import fetch as fetch_raw
from scraper.models import Job

DESCRIPTION_LIMIT = 500


def fetch(config: dict) -> list[Job]:
    company = config["company"]
    postings = fetch_raw(config)

    jobs = []
    for posting in postings:
        if not posting.get("isListed", True):
            continue
        jobs.append(
            Job(
                id=f"ashby:{company}:{posting['id']}",
                title=posting.get("title", ""),
                company=company,
                location=posting.get("location", ""),
                url=posting.get("jobUrl") or posting.get("applyUrl") or "",
                posted_at=posting.get("publishedAt"),
                description=_description(posting),
                source=f"ashby/{company}",
            )
        )
    return jobs


def _description(posting: dict) -> str:
    text = posting.get("descriptionPlain") or _strip_html(posting.get("descriptionHtml") or "")
    return " ".join(text.split())[:DESCRIPTION_LIMIT]


def _strip_html(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))
