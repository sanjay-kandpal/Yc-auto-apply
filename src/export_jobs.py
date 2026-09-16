from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from config_loader import ROOT, load_config
from db import all_jobs, all_resume_versions, connect, submitted_today, utc_now
from log_config import setup_logging

log = logging.getLogger(__name__)
OUT = ROOT / "data" / "jobs.json"
VERSIONS_OUT = ROOT / "data" / "resume_versions.json"

# Omit approval_token — HMAC material must not ship in the Worker snapshot.
JOB_FIELDS = (
    "id",
    "source",
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
    "match_breakdown",
    "resume_version_hash",
    "drafted_at",
    "confirmation_signal",
    "github_run_id",
    "apply_kind",
)
JSON_FIELDS = frozenset({"match_breakdown", "confirmation_signal"})


def _cell(row: sqlite3.Row, key: str):
    raw = row[key] if key in row.keys() else None
    if key not in JSON_FIELDS:
        return raw
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def snapshot(conn: sqlite3.Connection) -> dict:
    cfg = load_config()
    counts: dict[str, int] = {}
    jobs = []
    for row in all_jobs(conn):
        item = {key: _cell(row, key) for key in JOB_FIELDS}
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


def versions_snapshot(conn: sqlite3.Connection) -> dict:
    versions = {}
    for row in all_resume_versions(conn):
        versions[row["hash"]] = {
            "variant": row["variant"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
    return {"exported_at": utc_now(), "versions": versions}


def export(
    conn: sqlite3.Connection | None = None,
    path: Path | None = None,
    versions_path: Path | None = None,
) -> dict:
    close = False
    if conn is None:
        setup_logging()
        conn = connect()
        close = True
    data = snapshot(conn)
    versions = versions_snapshot(conn)
    if close:
        conn.close()
    out = path or OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    vout = versions_path or VERSIONS_OUT
    if path is not None and versions_path is None:
        vout = out.with_name("resume_versions.json")
    vout.parent.mkdir(parents=True, exist_ok=True)
    vout.write_text(json.dumps(versions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s (%s jobs) and %s (%s versions)", out, len(data["jobs"]), vout, len(versions["versions"]))
    return data


def main() -> None:
    export()


if __name__ == "__main__":
    main()
