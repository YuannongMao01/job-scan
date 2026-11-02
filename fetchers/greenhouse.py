"""Greenhouse board JSON, retaining complete descriptions and all fields."""

import html
import threading
import time

import requests

from fetchers._common import TIMEOUT_SECONDS, object_payload, records, slug

API_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
MAX_CONCURRENT_BOARDS = 4
REQUEST_INTERVAL = 0.25
_boards = threading.BoundedSemaphore(MAX_CONCURRENT_BOARDS)
_rate_lock = threading.Lock()
_last_start: float | None = None


def _wait_for_slot() -> None:
    global _last_start
    with _rate_lock:
        now = time.monotonic()
        if _last_start is not None:
            delay = REQUEST_INTERVAL - (now - _last_start)
            if delay > 0:
                time.sleep(delay)
        _last_start = time.monotonic()


def fetch(company: dict) -> list[dict]:
    url = API_URL.format(company=slug(company))
    with _boards:
        _wait_for_slot()
        response = requests.get(url, params={"content": "true"}, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        jobs = records(object_payload(response.json(), url)["jobs"], url)
    for job in jobs:
        if isinstance(job.get("content"), str):
            job["content"] = html.unescape(job["content"])
    return jobs
