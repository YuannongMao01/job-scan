"""Lever's complete, public postings list."""

import requests

from fetchers._common import TIMEOUT_SECONDS, records, slug

API_URL = "https://api.lever.co/v0/postings/{company}"


def fetch(company: dict) -> list[dict]:
    url = API_URL.format(company=slug(company))
    response = requests.get(url, params={"mode": "json"}, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return records(response.json(), url)
