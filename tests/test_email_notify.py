"""SMTP is mocked: no real mail, credentials or external service calls."""

import json
import smtplib
import sqlite3
import ssl
from dataclasses import replace
from unittest.mock import Mock, call

import pytest

import email_notify
from ingest import main
from normalizers.models import Job

JOB = Job("lever", "123", "Acme", "Engineer", "London", "JD", "https://example.com/123", None)


@pytest.fixture
def gmail(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test pass word only")
    monkeypatch.setenv("NOTIFY_TO", "recipient@example.com")
    smtp = Mock()
    smtp.send_message.return_value = {}
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=smtp)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(email_notify.smtplib, "SMTP", factory)
    return factory, smtp


def test_digest_tls_login_and_content(gmail):
    factory, smtp = gmail
    email_notify.send_email([JOB])
    factory.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
    assert smtp.method_calls[:4] == [
        call.ehlo(), call.starttls(context=smtp.starttls.call_args.kwargs["context"]),
        call.ehlo(), call.login("sender@example.com", "testpasswordonly"),
    ]
    context = smtp.starttls.call_args.kwargs["context"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    msg = smtp.send_message.call_args.args[0]
    assert str(msg["Subject"]) == "[Job Scan] 新增 1 个岗位"
    assert msg["From"] == "sender@example.com"
    assert msg["To"] == "recipient@example.com"
    body = msg.get_content()
    assert "Acme — Engineer" in body and "London" in body and JOB.apply_url in body
    assert msg.get_content_charset() == "utf-8"
    assert "testpasswordonly" not in msg.as_string()


def test_no_jobs_no_credentials_or_smtp(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    smtp = Mock()
    monkeypatch.setattr(email_notify.smtplib, "SMTP", smtp)
    email_notify.send_email([])
    smtp.assert_not_called()


def test_digest_lists_30_and_explicit_remainder(gmail):
    _, smtp = gmail
    jobs = [replace(JOB, title=f"Engineer-{i}", ats_job_id=str(i)) for i in range(31)]
    email_notify.send_email(jobs)
    body = smtp.send_message.call_args.args[0].get_content()
    assert "Engineer-29" in body and "Engineer-30" not in body
    assert "其余 1 个" in body
    assert "31" in str(smtp.send_message.call_args.args[0]["Subject"])


@pytest.mark.parametrize("key", ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NOTIFY_TO"])
def test_missing_settings_fail_without_smtp(gmail, monkeypatch, key):
    monkeypatch.setenv(key, " ")
    with pytest.raises(ValueError, match=key):
        email_notify.send_email([JOB])
    gmail[0].assert_not_called()


@pytest.mark.parametrize("value", ["not-an-email", "a@example.com\nBcc: other@example.com"])
def test_reject_invalid_recipient(gmail, monkeypatch, value):
    monkeypatch.setenv("NOTIFY_TO", value)
    with pytest.raises(ValueError, match="NOTIFY_TO"):
        email_notify.send_email([JOB])
    gmail[0].assert_not_called()


def snapshot(tmp_path):
    path = tmp_path / "raw.json"
    path.write_text(json.dumps({"batches": [{
        "source": {"type": "lever", "slug": "acme"},
        "jobs": [{"id": "123", "text": "AI Developer", "categories": {"location": "Shenzhen"},
                  "hostedUrl": JOB.apply_url}],
    }]}), encoding="utf-8")
    return path


def test_email_then_repeat_sends_only_once(gmail, tmp_path, capsys):
    args = ["--input", str(snapshot(tmp_path)), "--db", str(tmp_path / "seen.sqlite3"),
            "--send-email"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 1
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 0
    gmail[1].send_message.assert_called_once()


@pytest.mark.parametrize("stage", ["starttls", "login", "send_message"])
def test_failed_email_rolls_back_then_can_retry(gmail, tmp_path, capsys, caplog, stage):
    _, smtp = gmail
    method = getattr(smtp, stage)
    method.side_effect = smtplib.SMTPException("private server detail must not be logged")
    db = tmp_path / "seen.sqlite3"
    args = ["--input", str(snapshot(tmp_path)), "--db", str(db), "--send-email"]
    assert main(args) == 1
    assert capsys.readouterr().out == ""
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM seen").fetchone()[0] == 0
    assert "rolled back" in caplog.text
    assert "private server detail" not in caplog.text
    method.side_effect = None
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 1


def test_recipient_refusal_is_failure(gmail):
    gmail[1].send_message.return_value = {"recipient@example.com": (550, b"refused")}
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        email_notify.send_email([JOB])


def test_cli_validates_secrets_before_fetch_or_db(gmail, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    db = tmp_path / "seen.sqlite3"
    assert main(["--input", str(tmp_path / "missing.json"), "--db", str(db), "--send-email"]) == 1
    assert not db.exists()
    assert capsys.readouterr().out == ""
    gmail[0].assert_not_called()


def test_without_flag_retains_no_mail_mode(gmail, tmp_path, capsys):
    assert main(["--input", str(snapshot(tmp_path)), "--db", str(tmp_path / "seen.sqlite3")]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 1
    gmail[0].assert_not_called()
