from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from daily_report import (  # noqa: E402
    _in_window,
    _report_window,
    build_report_html,
    collect_report_jobs,
)
from log_config import setup_logging  # noqa: E402
from db import (  # noqa: E402
    all_resume_versions,
    connect,
    ensure_resume_version,
    insert_discovered,
    job_id_for,
    resume_hash,
    submitted_today,
    update_job,
    utc_now,
)
from export_jobs import JOB_FIELDS, export as export_jobs  # noqa: E402
from login import load_credentials  # noqa: E402
from draft import validate_draft, with_github  # noqa: E402
from match import empty_breakdown, evaluate_hard_filters, hard_filter_reason, score_job  # noqa: E402
from scrape import search_sources  # noqa: E402
from submit import confirmation_payload  # noqa: E402
from tokens import sign, verify  # noqa: E402
from record_video import build_object_key, object_key_allowed, recording_enabled, workflow_slug  # noqa: E402
from prune_recordings import tags_to_delete  # noqa: E402
from publish_release import release_page_url, release_tag  # noqa: E402
from resume_otp_email import html_body, subject_for, validate_otp  # noqa: E402
from waas_parse import walk_jobs  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def test_tokens() -> None:
    secret = "unit-secret"
    expiry = int(time.time()) + 60
    token = sign("job1", "approve", expiry, secret)
    assert verify("job1", "approve", expiry, token, secret)
    assert not verify("job1", "reject", expiry, token, secret)
    assert not verify("other", "approve", expiry, token, secret)
    assert not verify("job1", "view", expiry, token, secret)


def test_spectate_keys() -> None:
    old = os.environ.pop("RECORD_RUN", None)
    try:
        assert recording_enabled() is False
        assert workflow_slug("submit") == "submit"
        assert workflow_slug("scan") == "scan"
        key = build_object_key("scan", "1234567", day="2026-09-13")
        assert key == "2026-09-13/scan-1234567.mp4"
        assert object_key_allowed(key)
        assert object_key_allowed(build_object_key("YC submit", "88", day="2026-09-13"))
        assert not object_key_allowed("../secret.mp4")
        assert not object_key_allowed("2026-09-13/scan-123.webm")
        assert release_tag("123", "1") == "recording-123"
        assert release_tag("123", "2") == "recording-123-2"
        assert release_page_url("recording-123", "sanjay-kandpal/Yc-auto-apply").endswith(
            "/releases/tag/recording-123"
        )
        now = datetime(2026, 9, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
        entries = [
            {"tagName": "recording-fresh", "createdAt": "2026-09-13T11:00:00Z"},
            {"tagName": "v1.0.0", "createdAt": "2026-09-01T00:00:00Z"},
            {"tagName": "recording-old", "createdAt": "2026-09-12T11:00:00Z"},
            {"tagName": "recording-older", "createdAt": "2026-09-11T00:00:00Z"},
        ]
        assert tags_to_delete(entries, retain_hours=24, now=now) == [
            "recording-old",
            "recording-older",
        ]
        assert tags_to_delete(entries, retain_hours=24, keep=1, now=now) == [
            "recording-old",
            "recording-older",
        ]
        assert tags_to_delete(entries, retain_hours=48, keep=1, now=now) == [
            "recording-old",
            "recording-older",
        ]
        assert tags_to_delete(entries, retain_hours=72, keep=30, now=now) == []
    finally:
        if old is not None:
            os.environ["RECORD_RUN"] = old


def test_job_id_stable() -> None:
    a = job_id_for("Acme", "Backend", "https://www.workatastartup.com/jobs/1")
    b = job_id_for("acme", "backend", "https://www.workatastartup.com/jobs/1")
    assert a == b
    assert len(a) == 32


def test_walk_jobs() -> None:
    payload = {
        "companies": [
            {
                "name": "Acme",
                "jobs": [
                    {
                        "id": 100105,
                        "title": "Backend Engineer",
                        "description": "Remote Python APIs",
                        "show_path": "/jobs/100105",
                    }
                ],
            }
        ]
    }
    jobs = walk_jobs(payload)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["role"] == "Backend Engineer"
    assert jobs[0]["url"].endswith("/jobs/100105")


def test_hard_filter() -> None:
    cfg = {
        "filters": {
            "skip_keywords": ["intern"],
            "require_remote_or_india": True,
            "remote_or_india_keywords": ["remote", "india"],
            "role_keywords": ["backend", "software engineer"],
        }
    }
    assert hard_filter_reason("Engineering Intern", "remote python", cfg) == "skip_keyword"
    assert hard_filter_reason("Sales Lead", "remote closing deals", cfg) == "role_mismatch"
    assert hard_filter_reason("Backend Engineer", "on-site NYC only", cfg) == "not_remote_or_india"
    assert hard_filter_reason("Backend Engineer", "Remote Python APIs", cfg) is None


def test_match_breakdown_and_resume_versions() -> None:
    cfg = {
        "filters": {
            "skip_keywords": ["intern"],
            "require_remote_or_india": True,
            "remote_or_india_keywords": ["remote", "india"],
            "role_keywords": ["backend", "software engineer"],
        }
    }
    hard, reason = evaluate_hard_filters("Engineering Intern", "remote python", cfg)
    assert reason == "skip_keyword"
    assert hard["skip_keyword"] is False
    assert hard["passed"] is False
    skipped = empty_breakdown(hard, reason)
    assert skipped["final_score"] == 0.0
    assert skipped["variant_scores"] == {}
    assert "bonus" not in json.dumps(skipped)

    resumes = {
        "fullstack": "Python APIs React remote TypeScript backend engineer",
        "backend": "Python APIs remote backend engineer",
        "frontend": "React TypeScript CSS",
    }
    hard_ok, reason_ok = evaluate_hard_filters("Backend Engineer", "Remote Python APIs", cfg)
    assert reason_ok is None
    assert hard_ok["passed"] is True
    breakdown = score_job("Backend Engineer", "Remote Python APIs", resumes, hard=hard_ok)
    assert 0 <= breakdown["final_score"] <= 100
    assert breakdown["winning_variant"] in resumes
    assert set(breakdown["variant_scores"]) == set(resumes)
    assert breakdown["weights_version"] == "v1"
    assert "bonus" not in json.dumps(breakdown)
    for scores in breakdown["variant_scores"].values():
        assert set(scores) == {"cosine", "overlap", "combined"}

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "jobs.db")
        first = ensure_resume_version(conn, "backend", resumes["backend"])
        second = ensure_resume_version(conn, "backend", resumes["backend"])
        assert first == second == resume_hash(resumes["backend"])
        assert len(all_resume_versions(conn)) == 1
        other = ensure_resume_version(conn, "frontend", resumes["frontend"])
        assert other != first
        assert len(all_resume_versions(conn)) == 2
        conn.close()

    dry = json.loads(confirmation_payload(ok=False, text="dry-run: Send not clicked"))
    assert dry["ok"] is False
    assert "dry-run" in dry["text"]


def test_db_dedup_and_cap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "jobs.db"
        conn = connect(path)
        assert insert_discovered(conn, "Acme", "Backend", "https://example.com/j/1", "jd")
        assert not insert_discovered(conn, "Acme", "Backend", "https://example.com/j/1", "jd")
        conn.commit()
        job_id = job_id_for("Acme", "Backend", "https://example.com/j/1")
        update_job(conn, job_id, status="submitted", submitted_at=utc_now())
        conn.commit()
        assert submitted_today(conn) == 1
        conn.close()


def test_search_sources() -> None:
    sources = search_sources(
        {
            "url": "https://example.com/legacy",
            "max_pages": 20,
            "sources": [
                {"name": "remote_eng", "url": "https://example.com/remote", "max_pages": 20},
                {"name": "india_eng", "url": "https://example.com/india", "max_pages": 12},
                {"name": "exp_1_2", "url": "https://example.com/exp", "max_pages": 12},
            ],
        }
    )
    assert [s["name"] for s in sources] == ["remote_eng", "india_eng", "exp_1_2"]
    assert sources[1]["max_pages"] == 12
    fallback = search_sources({"url": "https://example.com/legacy", "max_pages": 8})
    assert fallback == [{"name": "default", "url": "https://example.com/legacy", "max_pages": 8}]


def test_with_github() -> None:
    cfg = {"github": {"profile_url": "https://github.com/sanjay-kandpal"}}
    text = with_github("I build APIs.", cfg)
    assert "https://github.com/sanjay-kandpal" in text
    again = with_github(text, cfg)
    assert again.count("github.com/sanjay-kandpal") == 1


def test_validate_draft() -> None:
    gh = "https://github.com/sanjay-kandpal"
    good = (
        "I ship FastAPI services for regulated workflows. "
        f"I want to help Acme scale the same way.\n\nGitHub: {gh}"
    )
    assert validate_draft(good, gh, max_sentences=2) == []
    bad = f"Hi there, I love your product. Thanks!\n\nGitHub: {gh}"
    fails = validate_draft(bad, gh, max_sentences=2)
    assert any("greeting" in f for f in fails)
    assert any("sign-off" in f for f in fails)


def test_report_window() -> None:
    start, end, date = _report_window(now=datetime(2026, 9, 10, 22, 0, tzinfo=IST))
    assert date == "2026-09-10"
    assert start == datetime(2026, 9, 9, 22, 0, tzinfo=IST)
    assert end == datetime(2026, 9, 10, 22, 0, tzinfo=IST)

    delayed = datetime(2026, 9, 11, 0, 22, tzinfo=IST)
    start, end, date = _report_window(now=delayed)
    assert date == "2026-09-10"
    assert start == datetime(2026, 9, 9, 22, 0, tzinfo=IST)
    assert end == datetime(2026, 9, 10, 22, 0, tzinfo=IST)

    morning = datetime(2026, 9, 11, 11, 54, tzinfo=IST)
    _, _, date = _report_window(now=morning)
    assert date == "2026-09-10"

    mixrank = "2026-09-10T08:59:03+00:00"
    loopfour = "2026-09-10T15:57:17+00:00"
    aiprise = "2026-09-09T09:38:55+00:00"
    assert _in_window(mixrank, start, end)
    assert _in_window(loopfour, start, end)
    assert not _in_window(aiprise, start, end)
    assert not _in_window(end.isoformat(), start, end)

    pinned_start, pinned_end, pinned_date = _report_window(close_date="2026-09-10")
    assert (pinned_start, pinned_end, pinned_date) == (start, end, "2026-09-10")


def test_report_includes_rejects() -> None:
    start, end, date = _report_window(close_date="2026-09-14")
    rows = [
        {
            "company": "Loop",
            "role": "Eng",
            "url": "https://example.com/loop",
            "status": "submitted",
            "submitted_at": "2026-09-14T08:00:00+00:00",
            "decided_at": "2026-09-14T08:00:00+00:00",
            "error_message": None,
        },
        {
            "company": "NoGo",
            "role": "Intern",
            "url": "https://example.com/nogo",
            "status": "rejected",
            "submitted_at": None,
            "decided_at": "2026-09-14T10:15:00+00:00",
            "error_message": None,
        },
        {
            "company": "OldRej",
            "role": "QA",
            "url": "https://example.com/old",
            "status": "rejected",
            "submitted_at": None,
            "decided_at": "2026-09-08T07:00:00+00:00",
            "error_message": None,
        },
        {
            "company": "Broken",
            "role": "SWE",
            "url": "https://example.com/fail",
            "status": "failed",
            "submitted_at": None,
            "decided_at": "2026-09-14T04:00:00+00:00",
            "error_message": "Send stayed disabled.",
        },
    ]
    submitted, rejected, failed = collect_report_jobs(rows, start, end)
    assert date == "2026-09-14"
    assert [j["company"] for j in submitted] == ["Loop"]
    assert [j["company"] for j in rejected] == ["NoGo"]
    assert [j["company"] for j in failed] == ["Broken"]
    html = build_report_html(submitted, failed, date, rejected=rejected)
    assert "1</strong> successfully applied" in html
    assert "1</strong> rejected" in html
    assert "1</strong> failed" in html
    assert "Rejected" in html
    assert "NoGo" in html
    assert "OldRej" not in html
    assert "Send stayed disabled." in html


def test_setup_logging_idempotent() -> None:
    import logging

    setup_logging()
    n = len(logging.getLogger().handlers)
    setup_logging()
    assert len(logging.getLogger().handlers) == n >= 1


def test_jobs_export_omits_token() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "jobs.db"
        json_path = Path(tmp) / "jobs.json"
        conn = connect(db_path)
        assert insert_discovered(conn, "Acme", "Backend", "https://example.com/j/1", "remote python")
        job_id = job_id_for("Acme", "Backend", "https://example.com/j/1")
        update_job(
            conn,
            job_id,
            status="pending_approval",
            match_score=42.5,
            resume_variant="backend",
            draft_answer="I build APIs.",
            sent_message="I build APIs.\n\nGitHub: https://github.com/sanjay-kandpal",
            approval_token="should-not-export",
        )
        conn.commit()
        data = export_jobs(conn, json_path)
        conn.close()
        job = data["jobs"][0]
        assert "approval_token" not in job
        assert set(job) == set(JOB_FIELDS)
        assert job["company"] == "Acme"
        assert job["draft_answer"] == "I build APIs."
        assert job["sent_message"].startswith("I build APIs.")
        assert job["match_score"] == 42.5
        assert job["match_breakdown"] is None
        assert data["daily_cap"] == 5
        assert data["counts"]["pending_approval"] == 1
        saved = json.loads(json_path.read_text(encoding="utf-8"))
        assert "approval_token" not in saved["jobs"][0]
        versions = json.loads(json_path.with_name("resume_versions.json").read_text(encoding="utf-8"))
        assert "versions" in versions


def test_sent_message_migrates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "jobs.db"
        raw = sqlite3.connect(db_path)
        raw.execute(
            """
            CREATE TABLE jobs (
              id TEXT PRIMARY KEY, company TEXT, role TEXT, url TEXT, jd_text TEXT,
              match_score REAL, resume_variant TEXT, draft_answer TEXT, status TEXT,
              approval_token TEXT, discovered_at TEXT, decided_at TEXT, submitted_at TEXT
            )
            """
        )
        raw.commit()
        raw.close()
        conn = connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "sent_message" in cols
        assert "error_message" in cols
        assert "match_breakdown" in cols
        assert "resume_version_hash" in cols
        assert "drafted_at" in cols
        assert "confirmation_signal" in cols
        assert "github_run_id" in cols
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "resume_versions" in tables
        conn.close()


def test_resume_otp_email() -> None:
    assert "password reset" in subject_for("password")
    assert "name reset" in subject_for("name")
    assert subject_for("password").startswith("YC resume login")
    html = html_body("123456", "password")
    assert "123456" in html
    assert "10 minutes" in html
    assert validate_otp("123456")
    assert not validate_otp("12345")
    assert not validate_otp("abcdef")
    assert not validate_otp("")


def test_load_credentials() -> None:
    old_email = os.environ.pop("YC_EMAIL", None)
    old_password = os.environ.pop("YC_PASSWORD", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.local.yaml"
            path.write_text("email: tester@example.com\npassword: unit-secret\n", encoding="utf-8")
            email, password = load_credentials(path)
            assert email == "tester@example.com"
            assert password == "unit-secret"
    finally:
        if old_email is not None:
            os.environ["YC_EMAIL"] = old_email
        if old_password is not None:
            os.environ["YC_PASSWORD"] = old_password


if __name__ == "__main__":
    test_tokens()
    test_spectate_keys()
    test_job_id_stable()
    test_walk_jobs()
    test_hard_filter()
    test_match_breakdown_and_resume_versions()
    test_db_dedup_and_cap()
    test_search_sources()
    test_with_github()
    test_validate_draft()
    test_report_window()
    test_report_includes_rejects()
    test_setup_logging_idempotent()
    test_jobs_export_omits_token()
    test_sent_message_migrates()
    test_resume_otp_email()
    test_load_credentials()
    print("all checks passed")
