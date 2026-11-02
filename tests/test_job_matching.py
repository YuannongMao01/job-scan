"""City gating, exclusion precedence, bilingual matching and ingestion wiring."""

import json
import sqlite3
from dataclasses import replace
from unittest.mock import Mock

import pytest

import ingest
from job_matching import load_filter
from normalizers.models import Job

JOB = Job("lever", "123", "Acme", "AI Developer", "Hong Kong", "", "https://example.com/123", None)


@pytest.mark.parametrize("location", [
    "Hong Kong", "香港", "SHANGHAI, China", "上海市", "Shenzhen", "深圳市南山区",
    "Hong-Kong SAR", "Ｈｏｎｇ　Ｋｏｎｇ", "Shanghai; Singapore",
])
def test_target_cities_pass(location):
    assert load_filter().rejection(replace(JOB, location=location)) is None


@pytest.mark.parametrize("location", [
    "", "China", "Remote", "Singapore", "Beijing", "New ShanghaiTown",
])
def test_other_or_unknown_locations_rejected(location):
    assert load_filter().rejection(replace(JOB, location=location)) == "location"


def test_city_in_jd_does_not_override_job_location():
    assert load_filter().rejection(
        replace(JOB, location="London", description="Our offices include Hong Kong and Shanghai")
    ) == "location"


@pytest.mark.parametrize("term", [
    "外包", "派遣", "STAFFING", "Recruiter", "Internship Program", "internship-programme",
])
@pytest.mark.parametrize("field", ["title", "description", "company", "department"])
def test_exclusions_win_even_when_include_matches(term, field):
    assert load_filter().rejection(replace(JOB, **{field: f"AI Developer {term}"})) == "excluded"


@pytest.mark.parametrize("title", [
    "AI Engineer", "Algorithm Engineer", "Algorithms Researcher", "Algorithmic Trading",
    "Fresh Grad", "New Graduate", "Graduation", "Developer", "Agentic Systems",
    "LLM", "RAG", "Harness", "Tool-use Engineer", "Workflow", "Planning",
    "開發工程師", "應用工程師", "算法", "大模型", "智能體", "具身",
    "大語言模型", "機器學習", "深度學習", "應屆畢業生", "工具調用", "工作流", "規劃",
    "Artificial Intelligence", "Machine Learning", "Retrieval-Augmented Generation",
    "ＡＩ工程師",
])
def test_bilingual_related_keywords(title):
    assert load_filter().rejection(replace(JOB, title=title)) is None


@pytest.mark.parametrize("title", ["Retail", "Detail Analyst", "Sustainable", "Said", "Ragtime"])
def test_short_tokens_do_not_match_inside_words(title):
    assert load_filter().rejection(replace(JOB, title=title)) == "keyword"


def test_include_can_be_in_jd_or_department_but_not_company():
    rules = load_filter()
    assert rules.rejection(replace(JOB, title="Engineer", description="Build LLM agents")) is None
    assert rules.rejection(replace(JOB, title="Engineer", department="人工智能")) is None
    assert rules.rejection(replace(JOB, title="Accountant", company="AI Labs")) == "keyword"


def test_phrase_cannot_span_fields():
    assert load_filter().rejection(replace(
        JOB, title="tool", department="use", description="No relevant experience",
    )) == "keyword"


@pytest.mark.parametrize("data", [
    "[]", "{}", "locations: []", "locations: [1]", "locations: [香港]\nexclude: []\ninclude: [AI]",
])
def test_bad_rules_raise_not_unfiltered(tmp_path, data):
    path = tmp_path / "filters.yaml"
    path.write_text(data, encoding="utf-8")
    with pytest.raises(ValueError):
        load_filter(path)


def test_filter_before_ledger_and_mail_then_second_run_zero(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"batches": [{
        "source": {"type": "lever", "slug": "acme"},
        "jobs": [
            {"id": "keep", "text": "AI Developer", "categories": {"location": "香港"},
             "hostedUrl": "https://example.com/keep"},
            {"id": "city", "text": "AI Developer", "categories": {"location": "London"},
             "hostedUrl": "https://example.com/city"},
            {"id": "exclude", "text": "AI staffing", "categories": {"location": "上海"},
             "hostedUrl": "https://example.com/exclude"},
            {"id": "keyword", "text": "Retail", "categories": {"location": "深圳"},
             "hostedUrl": "https://example.com/keyword"},
        ],
    }]}), encoding="utf-8")
    monkeypatch.setattr(ingest, "gmail_settings", lambda: None)
    send = Mock()
    monkeypatch.setattr(ingest, "send_email", send)
    db = tmp_path / "seen.sqlite3"
    args = ["--input", str(raw), "--db", str(db), "--send-email"]
    assert ingest.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert (first["fetched"], first["matched"], first["new"]) == (4, 1, 1)
    assert first["rejected"] == {"location": 1, "excluded": 1, "keyword": 1}
    assert [j.ats_job_id for j in send.call_args.args[0]] == ["keep"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT ats_job_id FROM seen").fetchall() == [("keep",)]
    assert ingest.main(args) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 0
    send.assert_called_once()
    # Explicit unfiltered mode still works and does not reset matched history.
    assert ingest.main(["--input", str(raw), "--db", str(db), "--no-filters"]) == 0
    unfiltered = json.loads(capsys.readouterr().out)
    assert unfiltered["matched"] == 4 and unfiltered["new"] == 3


def test_zero_matching_does_not_connect_to_smtp(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"batches": [{
        "source": {"type": "lever", "slug": "acme"},
        "jobs": [{"id": "1", "text": "AI Developer", "categories": {"location": "London"},
                  "hostedUrl": "https://example.com/1"}],
    }]}), encoding="utf-8")
    monkeypatch.setattr(ingest, "gmail_settings", lambda: None)
    send = Mock()
    monkeypatch.setattr(ingest, "send_email", send)
    assert ingest.main([
        "--input", str(raw), "--db", str(tmp_path / "seen.sqlite3"), "--send-email",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["fetched"] == 1 and result["matched"] == result["new"] == 0
    send.assert_not_called()
