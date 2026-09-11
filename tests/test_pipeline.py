from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from daily_report import _in_window, _report_window  # noqa: E402
from log_config import setup_logging  # noqa: E402
from db import connect, insert_discovered, job_id_for, submitted_today, update_job, utc_now  # noqa: E402
from login import load_credentials  # noqa: E402
from draft import validate_draft, with_github  # noqa: E402
from match import hard_filter_reason  # noqa: E402
from scrape import search_sources  # noqa: E402
from tokens import sign, verify  # noqa: E402
from waas_parse import walk_jobs  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def test_tokens() -> None:
    secret = "unit-secret"
    expiry = int(time.time()) + 60
    token = sign("job1", "approve", expiry, secret)
    assert verify("job1", "approve", expiry, token, secret)
    assert not verify("job1", "reject", expiry, token, secret)
    assert not verify("other", "approve", expiry, token, secret)


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


def test_setup_logging_idempotent() -> None:
    import logging

    setup_logging()
    n = len(logging.getLogger().handlers)
    setup_logging()
    assert len(logging.getLogger().handlers) == n >= 1


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
    test_job_id_stable()
    test_walk_jobs()
    test_hard_filter()
    test_db_dedup_and_cap()
    test_search_sources()
    test_with_github()
    test_validate_draft()
    test_report_window()
    test_setup_logging_idempotent()
    test_load_credentials()
    print("all checks passed")
