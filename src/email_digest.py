from __future__ import annotations

import html
import logging
import os

from dotenv import load_dotenv

from config_loader import load_config
from db import connect, jobs_with_status, parse_source_arg, update_job
from log_config import setup_logging
from mailer import send_html_email
from tokens import approval_link, canonical_source, sign, token_expiry

load_dotenv()
log = logging.getLogger(__name__)


def _card(job, approve: str, reject: str, source: str) -> str:
    answer = html.escape(job["draft_answer"] or "")
    company = html.escape(job["company"] or "")
    role = html.escape(job["role"] or "")
    url = html.escape(job["url"] or "")
    score = job["match_score"]
    variant = html.escape(job["resume_variant"] or "")
    board = html.escape(source)
    return f"""
    <div style="border:1px solid #ddd;border-radius:8px;padding:16px;margin:0 0 16px;font-family:sans-serif">
      <h2 style="margin:0 0 8px">{company} — {role}</h2>
      <p style="margin:0 0 8px">{board} · Match {score} · resume {variant} · <a href="{url}">listing</a></p>
      <p style="white-space:pre-wrap;background:#f7f7f7;padding:12px;border-radius:6px">{answer}</p>
      <p>
        <a href="{approve}" style="background:#0a7;color:#fff;padding:8px 14px;border-radius:4px;text-decoration:none">Approve</a>
        &nbsp;
        <a href="{reject}" style="background:#b33;color:#fff;padding:8px 14px;border-radius:4px;text-decoration:none">Reject</a>
      </p>
    </div>
    """


def _digest_subject(cfg: dict, source: str, n: int) -> str:
    if source == "wellfound":
        template = (
            (cfg.get("wellfound") or {}).get("email") or {}
        ).get("digest_subject") or "Wellfound jobs digest — {n} to review"
    else:
        template = cfg["email"]["digest_subject"]
    return str(template).format(n=n)


def send_digest(source: str = "yc") -> None:
    setup_logging()
    cfg = load_config()
    conn = connect()
    jobs = jobs_with_status(conn, "drafted", source=source)
    if not jobs:
        log.info("No drafted %s jobs to email.", source)
        conn.close()
        return

    secret = os.environ.get("APPROVAL_HMAC_SECRET", "").strip()
    if not secret:
        raise SystemExit("APPROVAL_HMAC_SECRET is not set.")
    base_url = cfg["email"]["approval_base_url"]
    if "YOUR_SUBDOMAIN" in base_url:
        raise SystemExit("Set email.approval_base_url in config.yaml to your Worker URL.")
    ttl = int(cfg["email"].get("token_ttl_hours", 48))
    expiry = token_expiry(ttl)
    link_source = canonical_source(source)

    cards = []
    tokens = []
    for job in jobs:
        token = sign(job["id"], "approve", expiry, secret, source=link_source)
        tokens.append((job["id"], token))
        cards.append(
            _card(
                job,
                approval_link(base_url, job["id"], "approve", expiry, secret, source=link_source),
                approval_link(base_url, job["id"], "reject", expiry, secret, source=link_source),
                source,
            )
        )

    subject = _digest_subject(cfg, source, len(jobs))
    extra = (
        " Wellfound Approve opens the listing and <strong>probes the Apply form only</strong> "
        "(cover-letter vs questions). Live Send is not implemented yet."
        if source == "wellfound"
        else " Each Approve click can trigger a real application."
    )
    body = (
        "<p style='font-family:sans-serif'>Approve only listings you have read."
        + extra
        + "</p>"
        + "".join(cards)
    )
    send_html_email(subject, body)
    for job_id, token in tokens:
        update_job(conn, job_id, status="pending_approval", approval_token=token)
    conn.commit()
    conn.close()
    log.info("Emailed %s %s jobs and marked pending_approval.", len(jobs), source)


if __name__ == "__main__":
    send_digest(parse_source_arg())
