from __future__ import annotations

import argparse
import html

from db import connect, get_job
from mailer import send_html_email


def notify(job_id: str) -> None:
    conn = connect()
    job = get_job(conn, job_id)
    conn.close()
    if not job:
        raise SystemExit(f"Unknown job_id: {job_id}")
    company = html.escape(job["company"] or "")
    role = html.escape(job["role"] or "")
    url = html.escape(job["url"] or "")
    status = job["status"]
    if status == "submitted":
        subject = f"Applied to {job['role']} at {job['company']}"
        body = f"<p>Submitted the application for <strong>{role}</strong> at <strong>{company}</strong>.</p>"
    elif status == "rejected":
        subject = f"Rejected {job['role']} at {job['company']}"
        body = f"<p>Marked rejected: <strong>{role}</strong> at <strong>{company}</strong>.</p>"
    elif status == "pending_approval":
        subject = f"Dry-run only: {job['role']} at {job['company']}"
        body = (
            "<p>Submit ran in dry-run mode, so nothing was sent. "
            "Set SUBMIT_DRY_RUN=false to allow live apply after Approve.</p>"
        )
    else:
        subject = f"Failed to submit — {job['role']} at {job['company']}"
        body = (
            f"<p>Failed to submit <strong>{role}</strong> at <strong>{company}</strong>. "
            "Needs manual follow-up.</p>"
        )
    body += f'<p><a href="{url}">Open listing</a></p>'
    send_html_email(subject, body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    notify(args.job_id)


if __name__ == "__main__":
    main()
