"""Normalize complete Lever postings, including lists and closing sections."""

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
    categories = record.get("categories") or {}
    parts = [plain_text(record.get("descriptionPlain")) or html_text(record.get("description"))]
    for section in record.get("lists") or []:
        parts.extend([plain_text(section.get("text")), html_text(section.get("content"))])
    parts.append(plain_text(record.get("additionalPlain")) or html_text(record.get("additional")))
    return Job(
        ats="lever",
        ats_job_id=job_id(record.get("id")),
        company=company_name(company),
        title=required_text(record.get("text"), "text"),
        location=categories.get("location") or "; ".join(categories.get("allLocations") or []),
        description=" ".join(part for part in parts if part),
        apply_url=required_text(record.get("hostedUrl") or record.get("applyUrl"), "hostedUrl"),
        posted_at=published_at(record.get("createdAt"), milliseconds=True),
        department=categories.get("department") or categories.get("team") or None,
        remote=remote_flag(record.get("workplaceType")),
    )
