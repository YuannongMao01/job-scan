from scraper.filters import build_predicates, keep
from scraper.models import Job


def make_job(title: str = "Software Engineer", location: str = "Remote (Canada)") -> Job:
    return Job(
        id="x:y:1",
        title=title,
        company="Acme",
        location=location,
        url="https://example.com",
        posted_at=None,
        description="",
        source="x/y",
    )


def test_empty_config_builds_no_predicates_and_keeps_everything():
    predicates = build_predicates({})
    assert predicates == []
    assert keep(make_job(), predicates)


def test_include_keywords_match_title_case_insensitively():
    predicates = build_predicates({"include_keywords": ["ENGINEER"]})
    assert keep(make_job("Senior Software Engineer"), predicates)
    assert not keep(make_job("Account Executive"), predicates)


def test_exclude_keywords_drop_matching_titles():
    predicates = build_predicates({"exclude_keywords": ["staff"]})
    assert not keep(make_job("Staff Engineer"), predicates)
    assert keep(make_job("Software Engineer"), predicates)


def test_locations_match_location_field():
    predicates = build_predicates({"locations": ["remote", "toronto"]})
    assert keep(make_job(location="Remote (Canada)"), predicates)
    assert keep(make_job(location="Toronto, ON"), predicates)
    assert not keep(make_job(location="London, England"), predicates)


def test_max_age_drops_stale_postings_and_keeps_fresh_or_undated():
    from datetime import UTC, datetime, timedelta

    predicates = build_predicates({"max_age_days": 14})
    now = datetime.now(UTC)

    def dated(days_ago: int) -> Job:
        job = make_job()
        return Job(
            **{
                **job.__dict__,
                "posted_at": (now - timedelta(days=days_ago)).isoformat(timespec="seconds"),
            }
        )

    assert keep(dated(3), predicates)  # fresh passes
    assert not keep(dated(30), predicates)  # month-old backfill dropped
    assert keep(make_job(), predicates)  # posted_at=None passes (never-miss)

    garbage = Job(**{**make_job().__dict__, "posted_at": "not-a-date"})
    assert keep(garbage, predicates)  # unparseable date passes


def test_max_age_handles_naive_timestamps():
    from datetime import datetime, timedelta

    predicates = build_predicates({"max_age_days": 14})
    naive_fresh = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    job = Job(**{**make_job().__dict__, "posted_at": naive_fresh})
    assert keep(job, predicates)


def test_rules_compose_and_do_not_leak_into_each_other():
    predicates = build_predicates(
        {"include_keywords": ["engineer"], "exclude_keywords": ["staff"]}
    )
    assert keep(make_job("Software Engineer"), predicates)
    assert not keep(make_job("Staff Engineer"), predicates)  # excluded wins
    assert not keep(make_job("Product Designer"), predicates)  # not included
    # regression: include must not accidentally use the exclude word list
    assert not keep(make_job("Staff Accountant"), predicates)
