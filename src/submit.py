from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from config_loader import load_config, repo_path
from db import connect, get_job, submitted_today, update_job, utc_now
from session import waas_context

load_dotenv()

EXTERNAL_HINTS = (
    "apply on company",
    "apply on their",
    "company website",
    "external application",
    "apply via",
)


def _dry_run(cli_dry: bool, cfg: dict) -> bool:
    if cli_dry:
        return True
    env = os.getenv("SUBMIT_DRY_RUN")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no")
    return bool(cfg["submit"].get("dry_run", True))


def _looks_external(page: Page) -> bool:
    body = page.inner_text("body").lower()
    return any(hint in body for hint in EXTERNAL_HINTS)


def _fill_message(page: Page, text: str) -> None:
    selectors = [
        "textarea",
        "textarea[name*='message' i]",
        "textarea[placeholder*='why' i]",
        "[contenteditable='true']",
    ]
    for selector in selectors:
        loc = page.locator(selector)
        if loc.count() == 0:
            continue
        box = loc.first
        try:
            box.click(timeout=3000)
            box.fill(text)
            return
        except Exception:
            continue
    raise RuntimeError("Could not find the application message field.")


def _resume_path(cfg: dict, variant: str) -> Path | None:
    files = cfg["submit"].get("resume_files") or cfg["submit"].get("resume_pdfs") or {}
    rel = files.get(variant) or files.get("fullstack")
    return repo_path(rel) if rel else None


def _message_text(job, resume_path: Path | None) -> str:
    draft = (job["draft_answer"] or "").strip()
    resume = ""
    if resume_path and resume_path.suffix.lower() == ".txt" and resume_path.exists():
        resume = resume_path.read_text(encoding="utf-8").strip()
    if draft and resume:
        return f"{draft}\n\n---\nResume\n{resume}"
    return draft or resume


def _upload_resume_if_present(page: Page, path: Path | None) -> None:
    if not path or not path.exists() or path.suffix.lower() != ".txt":
        print("Text-only apply (no file upload).")
        return
    file_input = page.locator("input[type='file']")
    if file_input.count() == 0:
        print("No file input on this form; using resume text in the message.")
        return
    try:
        file_input.first.set_input_files(str(path))
        print(f"Attached {path.name}")
    except Exception as exc:
        print(f"Skip file upload ({exc}); using resume text in the message.")


def _click_submit(page: Page) -> None:
    for label in ("Send", "Submit", "Apply", "Send application"):
        btn = page.get_by_role("button", name=label)
        if btn.count():
            btn.first.click()
            return
        link = page.get_by_role("link", name=label)
        if link.count():
            link.first.click()
            return
    raise RuntimeError("Could not find a Send/Submit/Apply button.")


def submit_job(job_id: str, cli_dry: bool = False) -> None:
    cfg = load_config()
    dry = _dry_run(cli_dry, cfg)
    cap = int(cfg["submit"].get("daily_cap", 5))
    conn = connect()
    job = get_job(conn, job_id)
    if not job:
        raise SystemExit(f"Unknown job_id: {job_id}")
    if job["status"] != "pending_approval":
        print(f"Skip submit: {job_id} is {job['status']} (need pending_approval).")
        conn.close()
        return
    already = submitted_today(conn)
    if already >= cap:
        print(f"Daily cap reached ({already}/{cap}). Not submitting {job_id}.")
        conn.close()
        return

    variant = job["resume_variant"] or "fullstack"
    resume_path = _resume_path(cfg, variant)
    message = _message_text(job, resume_path)
    if not message:
        conn.close()
        raise SystemExit(f"No draft or resume text for {job_id}.")
    print(f"{'DRY RUN' if dry else 'LIVE'} apply: {job['company']} — {job['role']}")

    try:
        with waas_context(headless=True) as context:
            page = context.new_page()
            page.goto(job["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            if _looks_external(page):
                raise RuntimeError("Listing looks like an external/company-site apply — skipped.")
            _fill_message(page, message)
            _upload_resume_if_present(page, resume_path)
            if dry:
                print("Dry-run: form filled, Send not clicked.")
            else:
                _click_submit(page)
                page.wait_for_timeout(3000)
            update_job(
                conn,
                job_id,
                status="submitted" if not dry else "pending_approval",
                decided_at=utc_now(),
                submitted_at=utc_now() if not dry else job["submitted_at"],
            )
            if dry:
                print("Dry-run left status as pending_approval.")
            else:
                print("Submitted.")
    except PlaywrightTimeout as exc:
        update_job(conn, job_id, status="failed", decided_at=utc_now())
        print(f"Failed (timeout): {exc}")
    except Exception as exc:
        update_job(conn, job_id, status="failed", decided_at=utc_now())
        print(f"Failed: {exc}")
    conn.commit()
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    submit_job(args.job_id, cli_dry=args.dry_run)


if __name__ == "__main__":
    main()
