"""SQLite persistence, atomicity and exactly-(ats, ats_job_id) deduplication."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ledger import connect, filter_new
from normalizers.models import Job

TODAY = datetime(2026, 11, 2, 8, tzinfo=UTC)
TOMORROW = datetime(2026, 11, 3, 8, tzinfo=UTC)
JOB = Job("lever", "123", "Acme", "Engineer", "Remote", "JD", "https://acme/jobs/123", None)


def test_twice_with_reopened_database_second_new_is_zero(tmp_path):
    path = tmp_path / "nested" / "seen_jobs.sqlite3"
    with closing(connect(path)) as conn:
        assert filter_new([JOB], conn, TODAY) == [JOB]
        first_seen = conn.execute("SELECT first_seen FROM seen").fetchone()[0]
    with closing(connect(path)) as conn:
        assert filter_new([JOB], conn, TOMORROW) == []
        assert conn.execute("SELECT count(*) FROM seen").fetchone()[0] == 1
        first, last = conn.execute("SELECT first_seen, last_seen FROM seen").fetchone()
        assert first == first_seen
        assert datetime.fromisoformat(last) == TOMORROW


def test_title_url_and_company_are_not_keys(tmp_path):
    with closing(connect(tmp_path / "seen.sqlite3")) as conn:
        assert filter_new([JOB], conn, TODAY) == [JOB]
        changed = replace(JOB, title="Senior Engineer", company="Acme Ltd", apply_url="https://new?x=1")
        assert filter_new([changed], conn, TOMORROW) == []
        assert conn.execute("SELECT company, title FROM seen").fetchone() == (
            "Acme Ltd", "Senior Engineer",
        )
        different_id = replace(JOB, ats_job_id="456")
        different_ats = replace(JOB, ats="ashby")
        assert filter_new([different_id, different_ats], conn, TOMORROW) == [
            different_id, different_ats,
        ]


def test_duplicates_within_same_batch_are_new_only_once(tmp_path):
    with closing(connect(tmp_path / "seen.sqlite3")) as conn:
        assert filter_new([JOB, JOB, JOB], conn, TODAY) == [JOB]
        assert conn.execute("SELECT count(*) FROM seen").fetchone()[0] == 1


def test_batch_failure_rolls_back_insert_and_update(tmp_path):
    with closing(connect(tmp_path / "seen.sqlite3")) as conn:
        filter_new([JOB], conn, TODAY)
        before = conn.execute("SELECT * FROM seen").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            filter_new([
                replace(JOB, title="Changed"),
                replace(JOB, ats_job_id="new"),
                replace(JOB, ats_job_id=""),
            ], conn, TOMORROW)
        assert conn.execute("SELECT * FROM seen").fetchall() == before


def test_same_title_and_url_different_ids_are_not_collapsed(tmp_path):
    with closing(connect(tmp_path / "seen.sqlite3")) as conn:
        assert len(filter_new([JOB, replace(JOB, ats_job_id="other")], conn, TODAY)) == 2


def test_sql_is_parameterized_and_description_is_not_stored(tmp_path):
    job = replace(JOB, title="O'Reilly'); DROP TABLE seen; --", description="private full JD")
    with closing(connect(tmp_path / "seen.sqlite3")) as conn:
        assert filter_new([job], conn, TODAY.isoformat()) == [job]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(seen)")}
        assert columns == {"ats", "ats_job_id", "company", "title", "first_seen", "last_seen"}
        assert conn.execute("SELECT title FROM seen").fetchone()[0] == job.title


def test_empty_batch_does_not_delete_history(tmp_path):
    with closing(connect(tmp_path / "seen.sqlite3")) as conn:
        filter_new([JOB], conn, TODAY)
        assert filter_new([], conn, TOMORROW) == []
        assert conn.execute("SELECT count(*) FROM seen").fetchone()[0] == 1


def test_concurrent_runs_only_one_reports_new(tmp_path):
    path = tmp_path / "seen.sqlite3"
    connect(path).close()
    barrier = threading.Barrier(2)

    def run(_):
        with closing(connect(path)) as conn:
            barrier.wait(timeout=10)
            return len(filter_new([JOB], conn, TODAY))

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(run, range(2))) == [0, 1]


def test_corrupt_database_is_not_reset(tmp_path):
    path = tmp_path / "broken.sqlite3"
    path.write_text("not a database", encoding="utf-8")
    with pytest.raises(sqlite3.DatabaseError):
        connect(path)
    assert path.read_text(encoding="utf-8") == "not a database"
