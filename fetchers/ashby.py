"""Ashby board JSON including HTML, workplace type and available compensation."""

import requests

from fetchers._common import TIMEOUT_SECONDS, object_payload, records, slug

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{company}"


def fetch(company: dict) -> list[dict]:
    url = API_URL.format(company=slug(company))
    response = requests.get(
        url, params={"includeCompensation": "true"}, timeout=TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return records(object_payload(response.json(), url)["jobs"], url)
