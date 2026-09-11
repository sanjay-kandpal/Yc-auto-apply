from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config_loader import ROOT
from log_config import setup_logging

log = logging.getLogger(__name__)
DB_PATH = ROOT / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  company TEXT,
  role TEXT,
  url TEXT,
  jd_text TEXT,
  match_score REAL,
  resume_variant TEXT,
  draft_answer TEXT,
  status TEXT,
  approval_token TEXT,
  discovered_at TEXT,
  decided_at TEXT,
  submitted_at TEXT,
  error_message TEXT
);
"""

ERROR_MESSAGE_MAX_LEN = 500


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def truncate_error(message: str, max_len: int = ERROR_MESSAGE_MAX_LEN) -> str:
    text = (message or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def job_id_for(company: str, role: str, url: str) -> str:
    raw = f"{company.strip().lower()}|{role.strip().lower()}|{url.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "error_message" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN error_message TEXT")


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute(SCHEMA)
    _migrate(conn)
    return conn


def init_db(path: Path | None = None) -> None:
    conn = connect(path)
    conn.commit()
    conn.close()


def insert_discovered(conn: sqlite3.Connection, company: str, role: str, url: str, jd_text: str) -> bool:
    job_id = job_id_for(company, role, url)
    try:
        conn.execute(
            """
            INSERT INTO jobs (id, company, role, url, jd_text, status, discovered_at)
            VALUES (?, ?, ?, ?, ?, 'discovered', ?)
            """,
            (job_id, company, role, url, jd_text, utc_now()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def jobs_with_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY discovered_at DESC", (status,)))


def all_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM jobs ORDER BY discovered_at DESC"))


def update_job(conn: sqlite3.Connection, job_id: str, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)


def submitted_today(conn: sqlite3.Connection) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM jobs
        WHERE status = 'submitted' AND submitted_at IS NOT NULL AND submitted_at LIKE ?
        """,
        (f"{today}%",),
    ).fetchone()
    return int(row["n"] if row else 0)


def mark_rejected(job_id: str) -> None:
    conn = connect()
    job = get_job(conn, job_id)
    if not job:
        raise SystemExit(f"Unknown job_id: {job_id}")
    if job["status"] not in ("pending_approval", "drafted"):
        log.info("Skip reject: %s is %s", job_id, job["status"])
        conn.close()
        return
    update_job(conn, job_id, status="rejected", decided_at=utc_now())
    conn.commit()
    conn.close()
    log.info("Rejected %s", job_id)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="SQLite helpers for the YC job bot")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--mark-rejected", dest="mark_rejected_id")
    args = parser.parse_args()
    if args.init:
        init_db()
        log.info("Initialized %s", DB_PATH)
        return
    if args.mark_rejected_id:
        mark_rejected(args.mark_rejected_id)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
