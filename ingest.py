"""Fetch or replay raw ATS batches, normalize and emit only SQLite-unseen jobs."""

import argparse
import json
import logging
import smtplib
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import requests
import yaml

from email_notify import gmail_settings, send_email
from fetchers import REGISTRY, get_fetcher
from fetchers._common import object_payload, records
from job_matching import DEFAULT_RULES, load_filter
from ledger import DEFAULT_DB, connect, filter_new
from normalizers import get_normalizer
from normalizers.models import Job

log = logging.getLogger("ingest")


def fetch_batches(path: Path) -> list[dict]:
    config = object_payload(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
    sources = records(config.get("sources"), str(path))
    batches: list[dict] = []
    for source in sources:
        ats = source.get("type")
        if ats not in REGISTRY:
            log.warning(
                "Skipping source type %r: Phase 2 ingestion supports only %s", ats, list(REGISTRY)
            )
            continue
        jobs = get_fetcher(ats)(source)
        log.info("%s/%s: fetched %d", ats, source.get("slug") or source.get("company"), len(jobs))
        batches.append({"source": source, "jobs": jobs})
    if not batches:
        raise ValueError(f"{path}: no sources for the four supported ATS providers")
    return batches


def normalize_batches(batches: list[dict]) -> list[Job]:
    if not batches:
        raise ValueError("Snapshot must contain at least one source batch")
    jobs: list[Job] = []
    for batch in batches:
        source = object_payload(batch.get("source"), "batch.source")
        normalize = get_normalizer(source["type"])
        for record in records(batch.get("jobs"), "batch.jobs"):
            jobs.append(normalize(record, source))
    return jobs


def job_json(job: Job) -> dict:
    result = asdict(job)
    result["posted_at"] = job.posted_at.isoformat() if job.posted_at is not None else None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sources", type=Path, help="Live YAML watchlist (four ATS types only)")
    source.add_argument("--input", type=Path, help="Replay a previously saved raw JSON snapshot")
    parser.add_argument("--snapshot", type=Path, help="Save full raw batches for exact replay")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Persistent SQLite seen ledger")
    rules = parser.add_mutually_exclusive_group()
    rules.add_argument(
        "--filters", type=Path, default=DEFAULT_RULES, help="Job matching YAML rules",
    )
    rules.add_argument("--no-filters", action="store_true", help="Explicit unfiltered ingestion")
    parser.add_argument(
        "--send-email", action="store_true", help="Email new jobs via Gmail STARTTLS"
    )
    args = parser.parse_args(argv)
    if args.snapshot and args.input:
        parser.error("--snapshot is only valid with --sources")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        job_filter = None if args.no_filters else load_filter(args.filters)
        if args.send_email:
            gmail_settings()
        if args.sources:
            batches = fetch_batches(args.sources)
        else:
            snapshot = object_payload(
                json.loads(args.input.read_text(encoding="utf-8")), str(args.input)
            )
            batches = records(snapshot.get("batches"), str(args.input))
        jobs = normalize_batches(batches)
        matched: list[Job] = []
        rejected: Counter[str] = Counter()
        for job in jobs:
            reason = job_filter.rejection(job) if job_filter else None
            if reason:
                rejected[reason] += 1
            else:
                matched.append(job)
        if args.snapshot:
            args.snapshot.parent.mkdir(parents=True, exist_ok=True)
            args.snapshot.write_text(
                json.dumps({"batches": batches}, ensure_ascii=True, indent=2), encoding="utf-8"
            )
        with closing(connect(args.db)) as conn:
            new_jobs = filter_new(
                matched, conn, datetime.now(UTC), on_new=send_email if args.send_email else None,
            )
        log.info(
            "fetched=%d matched=%d rejected=%s new=%d db=%s",
            len(jobs), len(matched), dict(rejected), len(new_jobs), args.db,
        )
        print(json.dumps({
            "fetched": len(jobs),
            "matched": len(matched),
            "rejected": dict(rejected),
            "new": len(new_jobs),
            "db": str(args.db),
            "jobs": [job_json(job) for job in new_jobs],
        }, ensure_ascii=True, indent=2))
    except smtplib.SMTPException as exc:
        log.error("Gmail delivery failed (%s); ledger changes rolled back", type(exc).__name__)
        return 1
    except (
        requests.RequestException, sqlite3.Error, OSError, ValueError, KeyError, yaml.YAMLError,
    ):
        log.exception("Ingestion failed; not reporting a successful run")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
