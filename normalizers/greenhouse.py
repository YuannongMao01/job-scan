"""Normalize records returned by fetchers.greenhouse (outer HTML already decoded)."""

from normalizers._common import (
    company_name,
    html_text,
    job_id,
    published_at,
    remote_flag,
    required_text,
)
from normalizers.models import Job


def normalize(record: dict, company: dict) -> Job:
    departments = [
        item["name"] for item in (record.get("departments") or []) if item.get("name")
    ]
    return Job(
        ats="greenhouse",
        ats_job_id=job_id(record.get("id")),
        company=company_name(company),
        title=required_text(record.get("title"), "title"),
        location=(record.get("location") or {}).get("name") or "",
        description=html_text(record.get("content")),
        apply_url=required_text(record.get("absolute_url"), "absolute_url"),
        posted_at=published_at(record.get("first_published") or record.get("first_published_at")),
        department="; ".join(departments) or None,
        remote=remote_flag(record.get("workplaceType"), record.get("isRemote")),
    )
