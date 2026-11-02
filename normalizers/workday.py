"""Normalize Workday list+detail records without interpreting relative dates."""

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
    info = (record.get("detail") or {}).get("jobPostingInfo") or {}
    path = required_text(record.get("externalPath"), "externalPath")
    if not path.startswith("/job/"):
        raise ValueError("Workday externalPath must start with /job/")
    host = company.get("host")
    if not host:
        tenant = company.get("tenant") or company["company"]
        host = f"{tenant}.{company['instance']}.myworkdayjobs.com"
    host = required_text(host, "host")
    site = required_text(company.get("site"), "site")
    department = info.get("department")
    if isinstance(department, dict):
        department = department.get("descriptor")
    return Job(
        ats="workday",
        ats_job_id=job_id(record.get("jobPostingId") or info.get("jobPostingId") or path),
        company=company_name(company),
        title=required_text(record.get("title") or info.get("title"), "title"),
        location=record.get("locationsText") or info.get("location") or "",
        description=html_text(info.get("jobDescription")),
        apply_url=f"https://{host}/en-US/{site}{path}",
        posted_at=published_at(info.get("startDate")),
        department=department or None,
        remote=remote_flag(info.get("workplaceType") or info.get("remoteType")),
    )
