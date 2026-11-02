"""Canonical mapping and publication-date regressions, fully offline."""

from dataclasses import fields
from datetime import UTC, datetime

import pytest
import responses

from fetchers.greenhouse import fetch
from normalizers import REGISTRY, ashby, get_normalizer, greenhouse, lever, workday
from normalizers._common import html_text, published_at, remote_flag
from normalizers.models import Job

GH = {
    "id": 123, "title": "Engineer", "location": {"name": "London"},
    "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
    "content": "<p>Research &amp; Development</p>",
    "first_published": "2026-10-30T12:00:00-04:00",
    "departments": [{"name": "Engineering"}, {"name": "Research"}],
}
LV = {
    "id": "l-123", "text": "Engineer", "categories": {"location": "Paris", "team": "Platform"},
    "descriptionPlain": "Hello <engineer> & team",
    "lists": [{"text": "Requirements", "content": "<ul><li>Python</li><li>SQL</li></ul>"}],
    "additionalPlain": "Closing remarks", "createdAt": 1793361600000,
    "hostedUrl": "https://jobs.lever.co/acme/l-123", "workplaceType": "remote",
}
ASH = {
    "id": "a-123", "title": "Engineer", "location": "Remote",
    "jobUrl": "https://jobs.ashbyhq.com/acme/a-123",
    "applyUrl": "https://jobs.ashbyhq.com/acme/a-123/application",
    "descriptionHtml": "<p>Build &amp; learn</p>", "department": "Product",
    "publishedAt": "2026-10-30T12:00:00Z", "workplaceType": "Hybrid",
}
WD = {
    "title": "Engineer", "externalPath": "/job/London/Engineer_123", "locationsText": "London",
    "postedOn": "Posted Today", "detail": {"jobPostingInfo": {
        "jobPostingId": "Engineer_123", "startDate": "2026-10-30",
        "jobDescription": "<p>Full Workday JD</p>", "remoteType": {"descriptor": "Remote"},
        "department": {"descriptor": "Technology"},
    }},
}
WD_COMPANY = {"host": "acme.wd1.myworkdayjobs.com", "tenant": "acme", "site": "Careers"}


def test_schema_exactly_matches_requested_fields():
    assert [f.name for f in fields(Job)] == [
        "ats", "ats_job_id", "company", "title", "location", "description", "apply_url",
        "posted_at", "department", "remote",
    ]
    assert set(REGISTRY) == {"greenhouse", "lever", "ashby", "workday"}
    with pytest.raises(KeyError, match="Unknown ATS"):
        get_normalizer("unknown")


def test_greenhouse_mapping_and_real_publication_not_update_time():
    job = greenhouse.normalize({**GH, "updated_at": "2026-11-02"}, {"slug": "acme"})
    assert isinstance(job, Job)
    assert job.ats_job_id == "123"
    assert job.company == "acme"
    assert job.title == "Engineer" and job.location == "London"
    assert job.description == "Research & Development"
    assert job.apply_url == GH["absolute_url"]
    assert job.posted_at == datetime(2026, 10, 30, 16, tzinfo=UTC)
    assert job.department == "Engineering; Research"
    assert job.remote is None


@responses.activate
def test_greenhouse_wire_to_job_does_not_double_unescape():
    responses.get(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        json={"jobs": [{
            **GH, "content": "&lt;p&gt;&amp;lt;engineer&amp;gt; &amp;amp; team&lt;/p&gt;",
        }]},
    )
    raw = fetch({"slug": "acme"})[0]
    assert raw["content"] == "<p>&lt;engineer&gt; &amp; team</p>"
    assert greenhouse.normalize(raw, {"slug": "acme"}).description == "<engineer> & team"


def test_lever_mapping_includes_all_description_sections():
    job = lever.normalize(LV, {"slug": "acme", "name": "Acme Ltd"})
    assert job.company == "Acme Ltd"
    assert job.ats == "lever" and job.ats_job_id == "l-123"
    assert job.location == "Paris" and job.department == "Platform"
    assert job.apply_url == LV["hostedUrl"]
    assert job.remote is True
    assert job.description == "Hello <engineer> & team Requirements Python SQL Closing remarks"
    assert job.posted_at == datetime.fromtimestamp(LV["createdAt"] / 1000, tz=UTC)


def test_lever_html_fallbacks_and_all_locations():
    job = lever.normalize({
        **LV, "descriptionPlain": None, "description": "<p>Intro &amp; text</p>",
        "additionalPlain": None, "additional": "<p>End</p>",
        "categories": {"allLocations": ["Paris", "Berlin"]},
    }, {"company": "acme"})
    assert job.location == "Paris; Berlin"
    assert job.description == "Intro & text Requirements Python SQL End"


def test_ashby_mapping_and_apply_fallback():
    job = ashby.normalize(ASH, {"company": "acme"})
    assert job.ats == "ashby" and job.ats_job_id == "a-123"
    assert job.description == "Build & learn"
    assert job.apply_url == ASH["jobUrl"]
    assert job.department == "Product" and job.remote is False
    assert job.posted_at == datetime(2026, 10, 30, 12, tzinfo=UTC)
    fallback = ashby.normalize({**ASH, "jobUrl": None}, {"company": "acme"})
    assert fallback.apply_url == ASH["applyUrl"]


def test_ashby_prefers_plain_text_without_parsing_it_as_html():
    job = ashby.normalize({
        **ASH, "descriptionPlain": "Use <T> & compare", "workplaceType": None, "isRemote": True,
    }, {"slug": "acme"})
    assert job.description == "Use <T> & compare" and job.remote is True


def test_workday_mapping_uses_detail_id_and_absolute_start_date():
    job = workday.normalize(WD, WD_COMPANY)
    assert job.ats == "workday" and job.ats_job_id == "Engineer_123"
    assert job.company == "acme" and job.location == "London"
    assert job.description == "Full Workday JD"
    assert job.apply_url == (
        "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/London/Engineer_123"
    )
    assert job.posted_at == datetime(2026, 10, 30, tzinfo=UTC)
    assert job.department == "Technology" and job.remote is True


def test_workday_relative_date_never_becomes_crawl_time():
    raw = {**WD, "detail": {}}
    job = workday.normalize(raw, {"company": "acme", "instance": "wd1", "site": "Careers"})
    assert job.ats_job_id == WD["externalPath"]
    assert job.posted_at is None
    assert job.description == "" and job.remote is None


def test_workday_explicit_id_takes_precedence():
    job = workday.normalize({**WD, "jobPostingId": "explicit"}, WD_COMPANY)
    assert job.ats_job_id == "explicit"


@pytest.mark.parametrize(("ats", "raw", "company", "field"), [
    ("greenhouse", GH, {"slug": "acme"}, "first_published"),
    ("lever", LV, {"slug": "acme"}, "createdAt"),
    ("ashby", ASH, {"slug": "acme"}, "publishedAt"),
])
def test_missing_publication_stays_none_even_with_update_time(ats, raw, company, field):
    job = get_normalizer(ats)({
        **raw, field: None, "updated_at": "2026-11-02", "updatedAt": "2026-11-02",
    }, company)
    assert job.posted_at is None


@pytest.mark.parametrize("value", [
    "0001-01-01", "0001-01-01T00:00:00Z", "0000-00-00", "1970-01-01T00:00:00Z",
    "1969-12-31", "not-a-date", "Posted Today", True, 0,
])
def test_invalid_or_sentinel_dates_become_null_not_now(value, caplog):
    assert published_at(value) is None
    assert "storing NULL, not crawl time" in caplog.text


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "0"])
def test_invalid_epoch_milliseconds_become_null(value, caplog):
    assert published_at(value, milliseconds=True) is None
    assert "storing NULL" in caplog.text


def test_absent_dates_are_not_errors(caplog):
    assert published_at(None) is None
    assert published_at("") is None
    assert not caplog.records


@pytest.mark.parametrize("value", [
    "2026-10-30", "2026-10-30T00:00:00", "2026-10-30T08:00:00+08:00",
])
def test_dates_are_timezone_aware_utc(value):
    assert published_at(value) == datetime(2026, 10, 30, tzinfo=UTC)


@pytest.mark.parametrize(("workplace", "fallback", "expected"), [
    ("Remote", None, True), ("remote", None, True), ("Hybrid", None, False),
    ("OnSite", None, False), ("On-site", None, False), (None, None, None),
    ("Unknown", None, None), (None, True, True), (None, "true", None),
])
def test_remote_is_structured_tristate(workplace, fallback, expected):
    assert remote_flag(workplace, fallback) is expected


@pytest.mark.parametrize(("ats", "raw", "company"), [
    ("greenhouse", GH, {"slug": "acme"}),
    ("lever", LV, {"slug": "acme"}),
    ("ashby", ASH, {"slug": "acme"}),
])
def test_missing_id_is_not_replaced_by_title_url_or_hash(ats, raw, company):
    with pytest.raises(ValueError, match="ats_job_id"):
        get_normalizer(ats)({**raw, "id": None}, company)


def test_descriptions_are_not_truncated():
    text = "Long description. " * 300
    job = greenhouse.normalize({**GH, "content": f"<p>{text}</p>"}, {"slug": "acme"})
    assert job.description == text.strip()
    assert html_text("<p>A<b>B</b>C</p><p>D</p><script>hidden</script>") == "ABC D"


@pytest.mark.parametrize(("ats", "fixture_name", "key"), [
    ("greenhouse", "greenhouse_duolingo.json", "jobs"),
    ("lever", "lever_palantir.json", None),
    ("ashby", "ashby_wealthsimple.json", "jobs"),
])
def test_recorded_fixture_contracts(ats, fixture_name, key, fixture):
    payload = fixture(fixture_name)
    records = payload[key] if key else payload
    jobs = [get_normalizer(ats)(record, {"company": "example"}) for record in records]
    assert len(jobs) == len(records)
    assert all(isinstance(job, Job) and job.ats_job_id and job.apply_url for job in jobs)
