"""Canonical full-fidelity job schema for raw ATS ingestion."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Job:
    ats: str
    ats_job_id: str
    company: str
    title: str
    location: str
    description: str
    apply_url: str
    posted_at: datetime | None
    department: str | None = None
    remote: bool | None = None
