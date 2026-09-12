from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from config_loader import ROOT, load_config
from db import all_jobs, connect, submitted_today, utc_now
from log_config import setup_logging

log = logging.getLogger(__name__)
OUT = ROOT / "data" / "jobs.json"

# Omit approval_token — HMAC material must not ship in the Worker snapshot.
JOB_FIELDS = (
    "id",
    "company",
    "role",
    "url",
    "jd_text",
    "match_score",
    "resume_variant",
    "draft_answer",
    "status",
    "discovered_at",
    "decided_at",
    "submitted_at",
    "error_message",
    "sent_message",
)


def snapshot(conn: sqlite3.Connection) -> dict:
    cfg = load_config()
    counts: dict[str, int] = {}
    jobs = []
    for row in all_jobs(conn):
        item = {key: (row[key] if key in row.keys() else None) for key in JOB_FIELDS}
        status = item["status"] or "unknown"
        counts[status] = counts.get(status, 0) + 1
        jobs.append(item)
    return {
        "exported_at": utc_now(),
        "daily_cap": int(cfg["submit"].get("daily_cap", 5)),
        "submitted_today": submitted_today(conn),
        "counts": counts,
        "jobs": jobs,
    }


def export(conn: sqlite3.Connection | None = None, path: Path | None = None) -> dict:
    close = False
    if conn is None:
        setup_logging()
        conn = connect()
        close = True
    data = snapshot(conn)
    if close:
        conn.close()
    out = path or OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s (%s jobs)", out, len(data["jobs"]))
    return data


def main() -> None:
    export()


if __name__ == "__main__":
    main()
