"""Raw payload contracts, pagination, request limits and standalone JSON output."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import responses

from fetchers import REGISTRY, ashby, get_fetcher, greenhouse, lever, workday
from fetchers.__main__ import main

BASE = "https://example.wd1.myworkdayjobs.com/wday/cxs/example/Careers"
COMPANY = {"host": "example.wd1.myworkdayjobs.com", "tenant": "example", "site": "Careers"}
DETAIL = {"jobPostingInfo": {"jobDescription": "<p>" + "Full JD " * 200 + "</p>"}}


@pytest.fixture(autouse=True)
def reset_greenhouse_clock(monkeypatch):
    monkeypatch.setattr(greenhouse, "_last_start", None)


def posting(index):
    return {"title": f"Engineer {index}", "externalPath": f"/job/City/Engineer_{index}"}


def add_page(offset, size, total=None):
    payload = {"jobPostings": [posting(i) for i in range(offset, offset + size)]}
    if total is not None:
        payload["total"] = total
    responses.post(
        f"{BASE}/jobs",
        json=payload,
        match=[responses.matchers.json_params_matcher({
            "appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "",
        })],
    )
    for item in payload["jobPostings"]:
        responses.get(BASE + item["externalPath"], json=DETAIL)


def test_registry():
    assert set(REGISTRY) == {"greenhouse", "lever", "ashby", "workday"}
    for name in REGISTRY:
        assert callable(get_fetcher(name))
    with pytest.raises(KeyError, match="Unknown ATS"):
        get_fetcher("unknown")


@responses.activate
def test_greenhouse_unescapes_once_without_truncating():
    content = "&lt;p&gt;" + "Full description " * 200 + "&amp;amp;&lt;/p&gt;"
    responses.get(
        greenhouse.API_URL.format(company="example"),
        json={"jobs": [{"id": 1, "content": content, "extra": {"unchanged": True}}]},
    )
    jobs = greenhouse.fetch({"slug": "example"})
    assert isinstance(jobs, list) and isinstance(jobs[0], dict)
    assert jobs[0]["content"] == "<p>" + "Full description " * 200 + "&amp;</p>"
    assert jobs[0]["extra"] == {"unchanged": True}
    assert parse_qs(urlsplit(responses.calls[0].request.url).query) == {"content": ["true"]}


@pytest.mark.parametrize(
    ("module", "url", "payload", "query"),
    [
        (lever, lever.API_URL, [{"workplaceType": "remote", "salaryRange": {"min": 100}}],
         {"mode": ["json"]}),
        (ashby, ashby.API_URL, {"jobs": [
            {"workplaceType": "Hybrid", "descriptionHtml": "<p>JD</p>", "isListed": False},
        ]}, {"includeCompensation": ["true"]}),
    ],
)
@responses.activate
def test_public_boards_preserve_all_fields(module, url, payload, query):
    responses.get(url.format(company="example"), json=payload)
    jobs = module.fetch({"company": "example"})
    assert jobs == (payload["jobs"] if isinstance(payload, dict) else payload)
    assert parse_qs(urlsplit(responses.calls[0].request.url).query) == query


@pytest.mark.parametrize("module", [greenhouse, lever, ashby])
@responses.activate
def test_get_boards_reject_http_errors(module):
    responses.get(module.API_URL.format(company="example"), status=429)
    with pytest.raises(requests.HTTPError):
        module.fetch({"slug": "example"})


@pytest.mark.parametrize("module", [greenhouse, lever, ashby])
@responses.activate
def test_get_boards_reject_malformed_payloads(module):
    responses.get(module.API_URL.format(company="example"), json={"jobs": {}})
    with pytest.raises(ValueError):
        module.fetch({"slug": "example"})


@pytest.mark.parametrize("module", [greenhouse, lever, ashby])
def test_get_boards_require_slug(module):
    with pytest.raises(ValueError, match="slug"):
        module.fetch({})


def test_greenhouse_request_spacing(monkeypatch):
    clock = [100.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(greenhouse.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(greenhouse.time, "sleep", sleep)
    for _ in range(5):
        greenhouse._wait_for_slot()
    assert waits == [0.25] * 4
    assert clock[0] == 101.0


def test_greenhouse_concurrency_never_exceeds_four(monkeypatch):
    lock = threading.Lock()
    active = peak = 0

    def get(*args, **kwargs):
        nonlocal active, peak
        assert kwargs["timeout"] == 30
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return Mock(json=lambda: {"jobs": []})

    monkeypatch.setattr(greenhouse, "_wait_for_slot", lambda: None)
    monkeypatch.setattr(greenhouse.requests, "get", get)
    with ThreadPoolExecutor(max_workers=12) as executor:
        assert list(executor.map(greenhouse.fetch, [{"slug": "example"}] * 12)) == [[]] * 12
    assert 1 <= peak <= 4


@responses.activate
def test_workday_paginates_and_retains_detail_and_headers():
    add_page(0, 20, total=23)
    add_page(20, 3, total=0)
    jobs = workday.fetch(COMPANY)
    assert len(jobs) == 23
    assert jobs[0]["title"] == "Engineer 0"
    assert jobs[-1]["detail"] == DETAIL
    assert len(jobs[0]["detail"]["jobPostingInfo"]["jobDescription"]) > 500
    for call in responses.calls:
        assert call.request.headers["User-Agent"] == "Mozilla/5.0"
        assert call.request.headers["Referer"] == (
            "https://example.wd1.myworkdayjobs.com/en-US/Careers"
        )
    posts = [c for c in responses.calls if c.request.method == "POST"]
    assert [json.loads(c.request.body)["offset"] for c in posts] == [0, 20]
    assert all(json.loads(c.request.body)["limit"] == 20 for c in posts)


@responses.activate
def test_workday_stops_at_total_even_on_full_page():
    add_page(0, 20, total=20)
    assert len(workday.fetch(COMPANY)) == 20
    assert len(responses.calls) == 21


@responses.activate
def test_workday_without_total_stops_on_short_page():
    add_page(0, 20)
    add_page(20, 1)
    assert len(workday.fetch(COMPANY)) == 21


@responses.activate
def test_workday_ignores_zero_total_on_later_pages():
    add_page(0, 20, total=41)
    add_page(20, 20, total=0)
    add_page(40, 1, total=0)
    assert len(workday.fetch(COMPANY)) == 41


@responses.activate
def test_workday_empty_board():
    add_page(0, 0, total=0)
    assert workday.fetch(COMPANY) == []


@responses.activate
def test_workday_warns_on_short_page_before_total(caplog):
    add_page(0, 1, total=40)
    assert len(workday.fetch(COMPANY)) == 1
    assert "short page" in caplog.text


@responses.activate
def test_workday_small_cap_warns_and_fetches_only_requested_details(caplog):
    responses.post(
        f"{BASE}/jobs", json={"jobPostings": [posting(i) for i in range(20)], "total": 20}
    )
    responses.get(BASE + posting(0)["externalPath"], json=DETAIL)
    assert len(workday.fetch({**COMPANY, "max_jobs": 1})) == 1
    assert len(responses.calls) == 2
    assert "results may be incomplete" in caplog.text


def test_workday_hard_cap_never_requests_offset_2000(monkeypatch, caplog):
    offsets = []

    def post(url, *, json, **kwargs):
        assert json["limit"] == 20
        assert kwargs["timeout"] == 30
        offset = json["offset"]
        offsets.append(offset)
        return Mock(json=lambda: {
            "jobPostings": [posting(i) for i in range(offset, offset + 20)], "total": 3000,
        })

    monkeypatch.setattr(workday.requests, "post", post)
    monkeypatch.setattr(workday.requests, "get", lambda *a, **k: Mock(json=lambda: DETAIL))
    assert len(workday.fetch(COMPANY)) == 2000
    assert offsets == list(range(0, 2000, 20))
    assert "results may be incomplete" in caplog.text


@responses.activate
def test_workday_legacy_source_config():
    add_page(0, 1, total=1)
    jobs = workday.fetch({"company": "example", "instance": "wd1", "site": "Careers"})
    assert len(jobs) == 1


@responses.activate
def test_workday_deduplicates_before_fetching_details(caplog):
    responses.post(
        f"{BASE}/jobs", json={"jobPostings": [posting(1), posting(1)], "total": 2}
    )
    responses.get(BASE + posting(1)["externalPath"], json=DETAIL)
    assert len(workday.fetch(COMPANY)) == 1
    assert "duplicate" in caplog.text
    assert len(responses.calls) == 2


@pytest.mark.parametrize("maximum", [0, 2001, True, "20"])
def test_workday_invalid_cap(maximum):
    with pytest.raises(ValueError, match="max_jobs"):
        workday.fetch({**COMPANY, "max_jobs": maximum})


@pytest.mark.parametrize("config", [
    {}, {**COMPANY, "host": "https://example.com"}, {**COMPANY, "site": "../invalid"},
])
def test_workday_invalid_config(config):
    with pytest.raises(ValueError):
        workday.fetch(config)


@responses.activate
def test_workday_list_http_error_is_not_an_empty_success():
    responses.post(f"{BASE}/jobs", status=403)
    with pytest.raises(requests.HTTPError):
        workday.fetch(COMPANY)


@pytest.mark.parametrize("detail", [None, {"jobPostingInfo": {}}])
@responses.activate
def test_workday_missing_jd_fails_explicitly(detail):
    responses.post(f"{BASE}/jobs", json={"jobPostings": [posting(0)], "total": 1})
    responses.get(BASE + posting(0)["externalPath"], json=detail)
    with pytest.raises(ValueError):
        workday.fetch(COMPANY)


@responses.activate
def test_workday_detail_http_error_is_not_a_partial_success():
    responses.post(f"{BASE}/jobs", json={"jobPostings": [posting(0)], "total": 1})
    responses.get(BASE + posting(0)["externalPath"], status=500)
    with pytest.raises(requests.HTTPError):
        workday.fetch(COMPANY)


@pytest.mark.parametrize("exception", [requests.Timeout, requests.ConnectionError])
def test_network_errors_propagate(monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception("offline")

    monkeypatch.setattr(lever.requests, "get", fail)
    with pytest.raises(exception):
        lever.fetch({"slug": "example"})


@responses.activate
def test_cli_stdout_is_raw_json(capsys):
    responses.get(lever.API_URL.format(company="example"), json=[{"id": 1}, {"id": 2}])
    assert main(["lever", "--slug", "example", "--sample", "0"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"id": 1}, {"id": 2}]


@responses.activate
def test_cli_empty_board_is_failed_acceptance(capsys, caplog):
    responses.get(lever.API_URL.format(company="example"), json=[])
    assert main(["lever", "--slug", "example"]) == 1
    assert capsys.readouterr().out == ""
    assert "No live jobs" in caplog.text


@responses.activate
def test_cli_http_error_is_nonzero(capsys, caplog):
    responses.get(lever.API_URL.format(company="example"), status=403)
    assert main(["lever", "--slug", "example"]) == 1
    assert capsys.readouterr().out == ""
    assert "fetch failed" in caplog.text


def test_cli_validates_required_arguments():
    with pytest.raises(SystemExit) as exc:
        main(["workday"])
    assert exc.value.code == 2
