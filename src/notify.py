from __future__ import annotations

import argparse
import html
import json
import logging
from pathlib import Path

from db import connect, get_job
from log_config import setup_logging
from mailer import send_html_email
from spectate_email import actions_run_url, recording_release_url

log = logging.getLogger(__name__)


def _job_dict(job) -> dict:
    return {key: job[key] for key in job.keys()}


def write_payload(job_id: str, path: Path) -> None:
    conn = connect()
    job = get_job(conn, job_id)
    conn.close()
    if not job:
        raise SystemExit(f"Unknown job_id: {job_id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_job_dict(job)), encoding="utf-8")
    log.info("Wrote notify payload for %s to %s", job_id, path)


def _load_job(job_id: str | None, payload_path: Path | None):
    if payload_path:
        return json.loads(payload_path.read_text(encoding="utf-8"))
    if not job_id:
        raise SystemExit("Need --job-id or --payload")
    conn = connect()
    job = get_job(conn, job_id)
    conn.close()
    return job


def _apply_kind(job) -> str:
    if isinstance(job, dict):
        return str(job.get("apply_kind") or "").strip()
    if hasattr(job, "keys") and "apply_kind" in job.keys():
        return str(job["apply_kind"] or "").strip()
    return ""


def _job_source(job) -> str:
    if isinstance(job, dict):
        return str(job.get("source") or "yc").strip().lower() or "yc"
    if hasattr(job, "keys") and "source" in job.keys():
        return str(job["source"] or "yc").strip().lower() or "yc"
    return "yc"


def notify(job_id: str | None = None, payload_path: Path | None = None) -> None:
    setup_logging()
    job = _load_job(job_id, payload_path)
    if not job:
        raise SystemExit(f"Unknown job_id: {job_id}")
    company = html.escape(job["company"] or "")
    role = html.escape(job["role"] or "")
    url = html.escape(job["url"] or "")
    status = job["status"]
    kind = html.escape(_apply_kind(job))
    kind_line = f"<p>apply_kind: <code>{kind or '—'}</code></p>"
    board = _job_source(job)
    if status == "submitted":
        board_label = "Wellfound" if board == "wellfound" else "YC"
        subject = f"Applied ({board_label}) to {job['role']} at {job['company']}"
        body = (
            f"<p>Submitted the {board_label} application for <strong>{role}</strong> at "
            f"<strong>{company}</strong>.</p>"
        )
        if kind:
            body += kind_line
    elif status == "rejected":
        subject = f"Rejected {job['role']} at {job['company']}"
        body = f"<p>Marked rejected: <strong>{role}</strong> at <strong>{company}</strong>.</p>"
    elif status == "pending_approval":
        subject = f"Dry-run only: {job['role']} at {job['company']}"
        body = (
            "<p>Submit ran in dry-run mode, so Send was not clicked. "
            "Approve on GitHub Actions is live (open JD → Apply Now → fill → Send application).</p>"
        )
    elif status == "approved":
        subject = f"Approved (not sent) — {job['role']} at {job['company']}"
        err = html.escape((job["error_message"] or "").strip() or "Application was not sent.")
        body = (
            f"<p>Approve recorded for <strong>{role}</strong> at <strong>{company}</strong>. "
            "No application was sent.</p>"
            f"{kind_line}"
            f"<p>{err}</p>"
        )
    else:
        subject = f"Failed to submit — {job['role']} at {job['company']}"
        err = html.escape((job["error_message"] or "").strip() or "No error recorded")
        body = (
            f"<p>Failed to submit <strong>{role}</strong> at <strong>{company}</strong>. "
            "Needs manual follow-up.</p>"
            f"{kind_line}"
            f"<p><strong>Error:</strong> {err}</p>"
        )
    body += f'<p><a href="{url}">Open listing</a></p>'
    recording = recording_release_url()
    if recording:
        body += (
            f'<p><a href="{html.escape(recording)}">Download this submit run</a> '
            "(mp4; GitHub does not play it inline)</p>"
        )
    run_url = actions_run_url()
    if run_url:
        body += f'<p><a href="{html.escape(run_url)}">GitHub Actions run</a></p>'
    send_html_email(subject, body)
    log.info("Notify %s for %s — %s", status, job["company"], job["role"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id")
    parser.add_argument("--payload")
    parser.add_argument("--write-payload")
    args = parser.parse_args()
    if args.write_payload:
        if not args.job_id:
            raise SystemExit("--write-payload needs --job-id")
        write_payload(args.job_id, Path(args.write_payload))
        return
    notify(args.job_id, Path(args.payload) if args.payload else None)


if __name__ == "__main__":
    main()
