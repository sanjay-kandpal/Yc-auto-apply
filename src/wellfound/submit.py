from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeout

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config_loader import load_config
from db import connect, get_job, submitted_today, truncate_error, update_job, utc_now
from draft import draft_note_for_job
from log_config import setup_logging
from wellfound.apply_flow import (
    ApplyFlowError,
    ApplyOutcome,
    confirmation_payload,
    open_job_and_read_jd,
    run_apply_after_draft,
)
from wellfound.session import wellfound_context

load_dotenv()
log = logging.getLogger(__name__)


def _submit_enabled(cfg: dict) -> bool:
    return bool(((cfg.get("wellfound") or {}).get("submit") or {}).get("enabled", False))


def _daily_cap(cfg: dict) -> int:
    return int(((cfg.get("wellfound") or {}).get("submit") or {}).get("daily_cap", 5))


def _github_run_id() -> str | None:
    return os.getenv("GITHUB_RUN_ID", "").strip() or None


def _fail(
    conn,
    job_id: str,
    *,
    apply_kind: str,
    reason: str,
    sent_message: str | None = None,
    signal: str | None = None,
) -> None:
    update_job(
        conn,
        job_id,
        status="failed",
        apply_kind=apply_kind,
        decided_at=utc_now(),
        error_message=truncate_error(reason),
        sent_message=sent_message,
        confirmation_signal=signal or confirmation_payload(ok=False, text=reason),
        github_run_id=_github_run_id(),
    )


def submit_job(job_id: str, *, headless: bool = True, dry_run: bool = False) -> None:
    """Open job JD → draft → Apply Now → fill → Send application."""
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

    if job["status"] not in ("pending_approval", "drafted", "approved", "failed"):
        log.info("Skip Wellfound submit: %s is %s", job_id, job["status"])
        conn.close()
        return

    enabled = _submit_enabled(cfg)
    if not enabled and not dry_run:
        _fail(
            conn,
            job_id,
            apply_kind="unknown",
            reason="wellfound.submit.enabled is false; refusing Send.",
        )
        conn.commit()
        conn.close()
        return

    already = submitted_today(conn, source="wellfound")
    cap = _daily_cap(cfg)
    if already >= cap and not dry_run:
        log.warning("Wellfound daily cap reached (%s/%s). Not submitting %s.", already, cap, job_id)
        conn.close()
        return

    url = (job["url"] or "").strip()
    if not url:
        _fail(conn, job_id, apply_kind="unknown", reason="Job has no URL.")
        conn.commit()
        conn.close()
        return

    message = ""
    try:
        with wellfound_context(headless=headless) as context:
            page = context.new_page()
            jd = open_job_and_read_jd(page, url)
            message = draft_note_for_job(
                cfg=cfg,
                company=job["company"] or "",
                role=job["role"] or "",
                jd=jd,
                resume_variant=job["resume_variant"] or "",
                role_kind="this role",
            )
            if not message.strip():
                raise RuntimeError("LLM draft was empty.")

            live = enabled and not dry_run
            outcome: ApplyOutcome = run_apply_after_draft(
                page, message, dry_run=not live
            )

            if not outcome.ok:
                _fail(
                    conn,
                    job_id,
                    apply_kind=outcome.apply_kind,
                    reason=outcome.reason,
                    sent_message=message,
                    signal=outcome.confirmation_signal,
                )
            elif outcome.dry_run or not live:
                update_job(
                    conn,
                    job_id,
                    status="pending_approval",
                    apply_kind=outcome.apply_kind,
                    decided_at=utc_now(),
                    error_message=truncate_error(
                        "Dry-run: open JD → Apply Now → fill done; Send not clicked."
                    ),
                    sent_message=message,
                    confirmation_signal=outcome.confirmation_signal,
                    github_run_id=_github_run_id(),
                )
                log.info("Wellfound dry-run filled note for %s (no Send).", job_id)
            else:
                update_job(
                    conn,
                    job_id,
                    status="submitted",
                    apply_kind="cover_letter_only",
                    decided_at=utc_now(),
                    submitted_at=utc_now(),
                    error_message=None,
                    sent_message=message,
                    confirmation_signal=outcome.confirmation_signal,
                    github_run_id=_github_run_id(),
                )
                log.info("Wellfound submitted %s — %s", job["company"], job["role"])
    except ApplyFlowError as exc:
        _fail(
            conn,
            job_id,
            apply_kind=exc.apply_kind,
            reason=exc.reason,
            sent_message=message or None,
        )
        log.exception("Wellfound apply flow error for %s", job_id)
    except PlaywrightTimeout as exc:
        _fail(
            conn,
            job_id,
            apply_kind="unknown",
            reason=f"timeout: {exc}",
            sent_message=message or None,
        )
        log.exception("Wellfound submit timeout for %s", job_id)
    except Exception as exc:
        _fail(
            conn,
            job_id,
            apply_kind="unknown",
            reason=str(exc),
            sent_message=message or None,
        )
        log.exception("Wellfound submit failed for %s", job_id)

    conn.commit()
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wellfound live apply (open JD → draft → Apply Now → Send application)"
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--headed", action="store_true", help="Run browser headed")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill the answer but do not click Send application",
    )
    args = parser.parse_args()
    submit_job(args.job_id, headless=not args.headed, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
