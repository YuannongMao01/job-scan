"""Durable, transactional seen-only ledger, keyed exclusively by ATS and job ID."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from normalizers.models import Job

DEFAULT_DB = Path("data") / "seen_jobs.sqlite3"


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen (
                    ats TEXT NOT NULL CHECK(length(trim(ats)) > 0),
                    ats_job_id TEXT NOT NULL CHECK(length(trim(ats_job_id)) > 0),
                    company TEXT NOT NULL,
                    title TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (ats, ats_job_id)
                )
            """)
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def filter_new(
    jobs: list[Job], conn: sqlite3.Connection, today: datetime | str,
    *, on_new: Callable[[list[Job]], None] | None = None,
) -> list[Job]:
    """Commit a whole batch atomically; preserve first_seen, refresh current metadata."""
    stamp = datetime.fromisoformat(today) if isinstance(today, str) else today
    stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    timestamp = stamp.isoformat(timespec="microseconds")
    new_jobs: list[Job] = []
    with conn:
        for job in jobs:
            inserted = conn.execute(
                """INSERT INTO seen (ats, ats_job_id, company, title, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (ats, ats_job_id) DO NOTHING""",
                (job.ats, job.ats_job_id, job.company, job.title, timestamp, timestamp),
            )
            if inserted.rowcount == 1:
                new_jobs.append(job)
            else:
                conn.execute(
                    """UPDATE seen SET company=?, title=?, last_seen=?
                       WHERE ats=? AND ats_job_id=?""",
                    (job.company, job.title, timestamp, job.ats, job.ats_job_id),
                )
        # Send before commit: a failed delivery must not suppress the next attempt.
        if new_jobs and on_new is not None:
            on_new(new_jobs)
    return new_jobs
