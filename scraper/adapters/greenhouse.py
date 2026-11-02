"""Greenhouse adapter.

Public, unauthenticated board API:
https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true

``content=true`` is required to get descriptions - without it the API
returns titles only.
"""

import html
import re

from fetchers.greenhouse import fetch as fetch_raw
from scraper.models import Job

DESCRIPTION_LIMIT = 500


def fetch(config: dict) -> list[Job]:
    company = config["company"]
    postings = fetch_raw(config)

    return [
        Job(
            id=f"greenhouse:{company}:{posting['id']}",
            title=posting.get("title", ""),
            company=company,
            location=(posting.get("location") or {}).get("name", ""),
            url=posting.get("absolute_url", ""),
            posted_at=posting.get("first_published") or posting.get("updated_at"),
            description=_description(posting.get("content") or ""),
            source=f"greenhouse/{company}",
        )
        for posting in postings
    ]


def _description(content: str) -> str:
    # The raw fetcher decoded the outer HTML escaping; decode text entities after stripping.
    text = re.sub(r"<[^>]+>", " ", content)
    return " ".join(html.unescape(text).split())[:DESCRIPTION_LIMIT]
