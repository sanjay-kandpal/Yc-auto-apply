from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from db import connect, insert_discovered, job_id_for, submitted_today, update_job, utc_now  # noqa: E402
from match import hard_filter_reason  # noqa: E402
from tokens import sign, verify  # noqa: E402
from waas_parse import walk_jobs  # noqa: E402


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


if __name__ == "__main__":
    test_tokens()
    test_job_id_stable()
    test_walk_jobs()
    test_hard_filter()
    test_db_dedup_and_cap()
    print("all checks passed")
