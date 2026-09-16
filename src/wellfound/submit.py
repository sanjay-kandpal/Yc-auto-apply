from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config_loader import load_config
from db import connect, get_job, submitted_today, truncate_error, update_job, utc_now
from log_config import setup_logging
from wellfound.apply_probe import ProbeResult, probe_apply_form
from wellfound.session import wellfound_context

load_dotenv()
log = logging.getLogger(__name__)

SEND_DEFERRED = (
    "Form probe: cover_letter_only. Live Send is deferred; no application was sent."
)


def _submit_enabled(cfg: dict) -> bool:
    return bool(((cfg.get("wellfound") or {}).get("submit") or {}).get("enabled", False))


def _daily_cap(cfg: dict) -> int:
    return int(((cfg.get("wellfound") or {}).get("submit") or {}).get("daily_cap", 5))


def _persist_probe(conn, job_id: str, result: ProbeResult, draft_answer: str | None) -> None:
    run_id = os.getenv("GITHUB_RUN_ID") or None
    decided = utc_now()
    if result.apply_kind == "cover_letter_only":
        update_job(
            conn,
            job_id,
            status="approved",
            apply_kind=result.apply_kind,
            decided_at=decided,
            error_message=truncate_error(SEND_DEFERRED),
            sent_message=(draft_answer or "").strip() or None,
            github_run_id=run_id,
        )
        return
    update_job(
        conn,
        job_id,
        status="failed",
        apply_kind=result.apply_kind,
        decided_at=decided,
        error_message=truncate_error(result.error_message()),
        github_run_id=run_id,
    )


def probe_job(job_id: str, *, headless: bool = True) -> None:
    """Login, open Apply, classify form. Never clicks Send."""
    setup_logging()
    cfg = load_config()
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
        log.info("Skip Wellfound probe: %s is %s", job_id, job["status"])
        conn.close()
        return

    # Cap is reserved for live Send; check still runs so wiring is ready.
    already = submitted_today(conn, source="wellfound")
    cap = _daily_cap(cfg)
    if already >= cap:
        log.warning(
            "Wellfound daily cap reached (%s/%s); probe still runs (Send remains disabled).",
            already,
            cap,
        )

    if _submit_enabled(cfg):
        # This stage never Sends even if someone flips the flag early.
        log.warning(
            "wellfound.submit.enabled is true, but this build only probes forms and refuses Send."
        )

    url = (job["url"] or "").strip()
    if not url:
        _persist_probe(
            conn,
            job_id,
            ProbeResult(apply_kind="unknown", reason="Job has no URL."),
            job["draft_answer"],
        )
        conn.commit()
        conn.close()
        return

    try:
        with wellfound_context(headless=headless) as context:
            page = context.new_page()
            result = probe_apply_form(page, url)
    except Exception as exc:
        log.exception("Wellfound apply probe failed for %s", job_id)
        result = ProbeResult(apply_kind="unknown", reason=f"Probe error: {exc}")

    _persist_probe(conn, job_id, result, job["draft_answer"])
    conn.commit()
    conn.close()
    log.info(
        "Wellfound probe %s → apply_kind=%s status written (no Send).",
        job_id,
        result.apply_kind,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wellfound apply form probe (classify only; no Send)"
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--headed", action="store_true", help="Run browser headed")
    args = parser.parse_args()
    probe_job(args.job_id, headless=not args.headed)


if __name__ == "__main__":
    main()
