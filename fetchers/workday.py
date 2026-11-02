"""Bounded Workday pagination with the complete detail payload per posting."""

import logging
import re

import requests

from fetchers._common import TIMEOUT_SECONDS, object_payload, records

log = logging.getLogger(__name__)
PAGE_SIZE = 20
MAX_JOBS = 2000


def fetch(company: dict) -> list[dict]:
    tenant = company.get("tenant") or company.get("company")
    host = company.get("host")
    if not host and tenant and company.get("instance"):
        host = f"{tenant}.{company['instance']}.myworkdayjobs.com"
    site = company.get("site")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("Workday host must be a hostname without a scheme or path")
    for name, value in (("tenant", tenant), ("site", site)):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError(f"Workday requires a valid {name}")
    maximum = company.get("max_jobs", MAX_JOBS)
    if type(maximum) is not int or not 1 <= maximum <= MAX_JOBS:
        raise ValueError(f"Workday max_jobs must be an integer from 1 to {MAX_JOBS}")
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://{host}/en-US/{site}",
    }
    jobs: list[dict] = []
    seen_paths: set[str] = set()
    offset = 0
    total: int | None = None
    while offset < maximum:
        response = requests.post(
            f"{base}/jobs",
            json={
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": company.get("search", ""),
            },
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = object_payload(response.json(), base)
        batch = records(data["jobPostings"], base)
        # Later pages on some tenants report total=0 despite containing jobs.
        if offset == 0:
            reported_total = data.get("total")
            if type(reported_total) is int and reported_total > 0:
                total = reported_total
        if len(batch) > PAGE_SIZE:
            raise ValueError(f"{base}: page exceeds the requested {PAGE_SIZE} jobs")
        for posting in batch[:maximum - offset]:
            path = posting.get("externalPath")
            if not isinstance(path, str) or not path.startswith("/job/"):
                raise ValueError(f"{base}: posting is missing a valid externalPath")
            if path in seen_paths:
                log.warning("%s: duplicate posting skipped: %s", base, path)
                continue
            detail_response = requests.get(
                f"{base}{path}", headers=headers, timeout=TIMEOUT_SECONDS
            )
            detail_response.raise_for_status()
            detail = object_payload(detail_response.json(), f"{base}{path}")
            info = object_payload(detail.get("jobPostingInfo"), f"{base}{path}")
            if not isinstance(info.get("jobDescription"), str):
                raise ValueError(f"{base}{path}: detail is missing jobDescription")
            jobs.append({**posting, "detail": detail})
            seen_paths.add(path)
        offset += len(batch)
        if offset >= maximum and (offset > maximum or total is None or offset < total):
            break
        if len(batch) < PAGE_SIZE:
            if total is not None and offset < total:
                log.warning("%s: short page at %d before reported total %d", base, offset, total)
            return jobs
        if total is not None and offset >= total:
            return jobs
    log.warning(
        "%s: stopped at safety/configured cap %d (reported total %s); results may be incomplete",
        base, maximum, total,
    )
    return jobs
