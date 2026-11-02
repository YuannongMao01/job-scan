"""Exercise the actual workflow commit step against disposable local Git remotes."""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from ledger import connect, filter_new
from normalizers.models import Job

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.load(
    (ROOT / ".github" / "workflows" / "daily-scan.yml").read_text(encoding="utf-8"),
    Loader=yaml.BaseLoader,
)
COMMIT = next(s for s in WORKFLOW["jobs"]["scan"]["steps"] if s.get("name") == "Commit ledger")
JOB = Job("lever", "123", "Acme", "Engineer", "", "", "https://example.com/123", None)


def git(cwd, *args):
    return subprocess.run(
        ["git", "--no-pager", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    remote = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(repo))
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    shutil.copyfile(ROOT / ".gitignore", repo / ".gitignore")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "Initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return repo, remote


def run_commit(repo):
    bash = shutil.which("bash")
    if os.name == "nt":
        git_exe = shutil.which("git")
        candidate = Path(git_exe).parent.parent / "bin" / "bash.exe" if git_exe else None
        if candidate and candidate.is_file():
            bash = str(candidate)
    if not bash:
        pytest.skip("Bash is required to exercise the GitHub-hosted Ubuntu commit step")
    # The job uses setup-python; expose the same Python executable to Git Bash.
    import sys

    env = {**os.environ, "LEDGER_BRANCH": "main"}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", COMMIT["run"]],
        cwd=repo, env=env, capture_output=True, text=True,
    )


def add_job(repo):
    conn = connect(repo / "data" / "seen_jobs.sqlite3")
    try:
        filter_new([JOB], conn, "2026-11-02T08:00:00+00:00")
    finally:
        conn.close()


def test_daily_schedule_permissions_and_no_duplicate_cron():
    assert WORKFLOW["on"]["schedule"] == [{"cron": "17 1 * * *"}]
    assert "workflow_dispatch" in WORKFLOW["on"]
    assert WORKFLOW["permissions"] == {"contents": "write"}
    assert WORKFLOW["concurrency"] == {"group": "hunter-state", "cancel-in-progress": "false"}
    legacy = yaml.load(
        (ROOT / ".github" / "workflows" / "poll.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert "schedule" not in legacy["on"]
    assert legacy["concurrency"] == WORKFLOW["concurrency"]
    scan = next(s for s in WORKFLOW["jobs"]["scan"]["steps"] if s.get("name") == "Run scan")
    assert "--sources sources.yaml" in scan["run"]
    assert "python -m ingest" in scan["run"]
    assert "--send-email" in scan["run"]
    assert scan["env"] == {
        key: "${{ secrets." + key + " }}"
        for key in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NOTIFY_TO")
    }
    assert "TELEGRAM" not in scan.get("env", {})
    assert "|| true" not in COMMIT["run"]
    recovery = WORKFLOW["jobs"]["scan"]["steps"][-1]
    assert recovery["if"] == "always()"
    assert "data/seen_jobs.sqlite3" in recovery["with"]["path"]


def test_commit_push_and_recheckout_retains_dedup(repository, tmp_path):
    repo, remote = repository
    add_job(repo)
    # No force-add is needed; only the intended ledger is allowlisted.
    assert not git(repo, "ls-files", "data/seen_jobs.sqlite3")
    result = run_commit(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "ls-files", "data/seen_jobs.sqlite3") == "data/seen_jobs.sqlite3"
    assert "Co-authored-by: Copilot" in git(repo, "log", "-1", "--format=%B")
    clone = tmp_path / "next-run"
    git(tmp_path, "clone", "--branch", "main", str(remote), str(clone))
    conn = connect(clone / "data" / "seen_jobs.sqlite3")
    try:
        assert filter_new([JOB], conn, "2026-11-03T08:00:00+00:00") == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    before = git(repo, "rev-parse", "HEAD")
    result = run_commit(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Ledger unchanged" in result.stdout
    assert git(repo, "rev-parse", "HEAD") == before


def test_missing_ledger_fails_without_commit(repository):
    repo, _ = repository
    before = git(repo, "rev-parse", "HEAD")
    result = run_commit(repo)
    assert result.returncode != 0
    assert "Ledger missing" in result.stderr
    assert git(repo, "rev-parse", "HEAD") == before


def test_corrupt_ledger_fails_without_commit(repository):
    repo, _ = repository
    path = repo / "data" / "seen_jobs.sqlite3"
    path.parent.mkdir()
    path.write_text("not SQLite", encoding="utf-8")
    before = git(repo, "rev-parse", "HEAD")
    result = run_commit(repo)
    assert result.returncode != 0
    assert git(repo, "rev-parse", "HEAD") == before


def test_rejected_push_is_a_failure_and_preserves_remote_ledger(repository, tmp_path):
    repo, remote = repository
    add_job(repo)
    result = run_commit(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    other = tmp_path / "other"
    git(tmp_path, "clone", "--branch", "main", str(remote), str(other))
    git(other, "config", "user.name", "Test")
    git(other, "config", "user.email", "test@example.com")
    (other / "change.txt").write_text("Concurrent update", encoding="utf-8")
    git(other, "add", "change.txt")
    git(other, "commit", "-m", "Concurrent change")
    git(other, "push", "origin", "main")
    remote_before = git(other, "rev-parse", "HEAD")
    with sqlite3.connect(repo / "data" / "seen_jobs.sqlite3") as conn:
        conn.execute("UPDATE seen SET title='Changed'")
    result = run_commit(repo)
    assert result.returncode != 0
    assert "rejected" in result.stderr
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == remote_before
