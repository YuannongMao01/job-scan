"""CLI integration: raw snapshot -> canonical Job -> persistent SQLite ledger."""

import json
import sqlite3

import responses

from ingest import main


def snapshot(path, records=None):
    records = [{"id": "123", "text": "AI Developer", "categories": {"location": "Hong Kong"},
                "hostedUrl": "https://jobs.lever.co/acme/123"}] \
        if records is None else records
    path.write_text(json.dumps({"batches": [{
        "source": {"type": "lever", "slug": "acme"}, "jobs": records,
    }]}), encoding="utf-8")
    return path


def test_cli_replay_twice_second_added_zero(tmp_path, capsys):
    raw = snapshot(tmp_path / "raw.json")
    db = tmp_path / "data" / "seen.sqlite3"
    args = ["--input", str(raw), "--db", str(db)]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["fetched"] == 1 and first["new"] == 1
    assert first["jobs"][0]["posted_at"] is None
    assert first["jobs"][0]["ats_job_id"] == "123"
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["fetched"] == 1 and second["new"] == 0 and second["jobs"] == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM seen").fetchone()[0] == 1


@responses.activate
def test_live_fetch_snapshot_and_offline_replay_share_one_ledger(tmp_path, capsys, caplog):
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n  - {type: lever, slug: acme}\n  - {type: github, repo: ignored}\n",
        encoding="utf-8",
    )
    responses.get(
        "https://api.lever.co/v0/postings/acme",
        json=[{"id": "123", "text": "AI Developer", "categories": {"location": "Shanghai"},
               "hostedUrl": "https://jobs.lever.co/acme/123",
               "createdAt": 1793361600000}],
    )
    raw = tmp_path / "raw.json"
    db = tmp_path / "seen.sqlite3"
    assert main(["--sources", str(config), "--snapshot", str(raw), "--db", str(db)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["new"] == 1
    assert result["jobs"][0]["posted_at"].endswith("+00:00")
    assert "Skipping source type" in caplog.text
    assert main(["--input", str(raw), "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 0
    assert len(responses.calls) == 1


def test_normalization_failure_does_not_mark_any_jobs_seen(tmp_path, capsys, caplog):
    raw = snapshot(tmp_path / "raw.json", [
        {"id": "123", "text": "Engineer", "hostedUrl": "https://jobs.lever.co/acme/123"},
        {"text": "Missing ID", "hostedUrl": "https://jobs.lever.co/acme/noid"},
    ])
    db = tmp_path / "seen.sqlite3"
    assert main(["--input", str(raw), "--db", str(db)]) == 1
    assert capsys.readouterr().out == ""
    assert not db.exists()
    assert "Ingestion failed" in caplog.text


def test_corrupt_db_does_not_report_new_zero(tmp_path, capsys, caplog):
    raw = snapshot(tmp_path / "raw.json")
    db = tmp_path / "seen.sqlite3"
    db.write_text("broken database", encoding="utf-8")
    assert main(["--input", str(raw), "--db", str(db)]) == 1
    assert capsys.readouterr().out == ""
    assert "Ingestion failed" in caplog.text


def test_invalid_snapshot_is_an_explicit_failure(tmp_path, capsys, caplog):
    raw = tmp_path / "raw.json"
    raw.write_text('{"batches": {}}', encoding="utf-8")
    assert main(["--input", str(raw), "--db", str(tmp_path / "seen.sqlite3")]) == 1
    assert capsys.readouterr().out == ""
    assert "expected a JSON list" in caplog.text


@responses.activate
def test_failed_fetch_does_not_create_ledger(tmp_path, capsys):
    config = tmp_path / "sources.yaml"
    config.write_text("sources:\n  - {type: lever, slug: acme}", encoding="utf-8")
    responses.get("https://api.lever.co/v0/postings/acme", status=503)
    db = tmp_path / "seen.sqlite3"
    assert main(["--sources", str(config), "--db", str(db)]) == 1
    assert capsys.readouterr().out == ""
    assert not db.exists()
