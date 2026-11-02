"""Normalize Ashby records without filtering or truncating them."""

from normalizers._common import (
    company_name,
    html_text,
    job_id,
    plain_text,
    published_at,
    remote_flag,
    required_text,
)
from normalizers.models import Job


def normalize(record: dict, company: dict) -> Job:
    return Job(
        ats="ashby",
        ats_job_id=job_id(record.get("id")),
        company=company_name(company),
        title=required_text(record.get("title"), "title"),
        location=record.get("location") or "",
        description=plain_text(record.get("descriptionPlain"))
        or html_text(record.get("descriptionHtml")),
        apply_url=required_text(record.get("jobUrl") or record.get("applyUrl"), "jobUrl/applyUrl"),
        posted_at=published_at(record.get("publishedAt")),
        department=record.get("department") or record.get("team") or None,
        remote=remote_flag(record.get("workplaceType"), record.get("isRemote")),
    )
