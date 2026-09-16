from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from db import connect, get_job, truncate_error, update_job, utc_now
from log_config import setup_logging

log = logging.getLogger(__name__)

STUB_MESSAGE = (
    "Wellfound live Send is not implemented. Approve recorded only; no application was sent."
)


def approve_stub(job_id: str) -> None:
    setup_logging()
    conn = connect()
    job = get_job(conn, job_id)
    if not job:
        conn.close()
        raise SystemExit(f"Unknown job_id: {job_id}")
    keys = job.keys()
    source = job["source"] if "source" in keys else "yc"
    if source != "wellfound":
        conn.close()
        raise SystemExit(f"Job {job_id} is source={source}, not wellfound")
    if job["status"] not in ("pending_approval", "drafted", "approved"):
        log.info("Skip Wellfound approve stub: %s is %s", job_id, job["status"])
        conn.close()
        return
    update_job(
        conn,
        job_id,
        status="approved",
        decided_at=utc_now(),
        error_message=truncate_error(STUB_MESSAGE),
        github_run_id=os.getenv("GITHUB_RUN_ID") or None,
    )
    conn.commit()
    conn.close()
    log.info("Wellfound approve stub recorded for %s (no live Send).", job_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wellfound submit stub (no live Send)")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    approve_stub(args.job_id)


if __name__ == "__main__":
    main()
