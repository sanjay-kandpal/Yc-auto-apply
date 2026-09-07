from __future__ import annotations

import argparse
import os

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
    if env is not None and env.strip():
        return env.strip().lower() not in ("0", "false", "no")
    return bool(cfg["submit"].get("dry_run", False))


def _looks_external(page: Page) -> bool:
    body = page.inner_text("body").lower()
    return any(hint in body for hint in EXTERNAL_HINTS)


def _message_box(page: Page):
    specific = page.locator("textarea[placeholder*='Hi! My name is']")
    if specific.count():
        return specific.first
    return page.locator("textarea").first


def _form_is_open(page: Page) -> bool:
    box = page.locator("textarea[placeholder*='Hi! My name is']")
    try:
        return box.count() > 0 and box.first.is_visible()
    except Exception:
        return False


def _click_apply(page: Page) -> None:
    if _form_is_open(page):
        return
    candidates = (
        page.get_by_role("link", name="Apply", exact=True),
        page.get_by_role("button", name="Apply", exact=True),
        page.locator("a", has_text="Apply"),
        page.locator("button", has_text="Apply"),
    )
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=5000)
                page.wait_for_timeout(1500)
                if _form_is_open(page) or page.locator("textarea").count():
                    return
        except Exception:
            continue
    raise RuntimeError("Could not find the Apply control that opens the message form.")


def _fill_message(page: Page, text: str) -> None:
    box = _message_box(page)
    box.wait_for(state="visible", timeout=15000)
    box.click(timeout=5000)
    box.fill(text)
    try:
        box.dispatch_event("input")
        box.dispatch_event("change")
    except Exception:
        pass


def _message_text(job) -> str:
    draft = (job["draft_answer"] or "").strip()
    if draft:
        return draft
    cfg = load_config()
    variant = job["resume_variant"] or "fullstack"
    files = cfg["submit"].get("resume_files") or {}
    rel = files.get(variant) or files.get("fullstack")
    if not rel:
        return ""
    path = repo_path(rel)
    if path.exists() and path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8").strip()
    return ""


def _click_send(page: Page) -> None:
    btn = page.get_by_role("button", name="Send", exact=True)
    btn.first.wait_for(state="visible", timeout=10000)
    page.wait_for_function(
        """() => {
            const buttons = [...document.querySelectorAll('button')];
            const send = buttons.find((b) => (b.textContent || '').trim() === 'Send');
            return Boolean(send && !send.disabled);
        }""",
        timeout=10000,
    )
    btn.first.click()


def submit_job(job_id: str, cli_dry: bool = False) -> None:
    cfg = load_config()
    dry = _dry_run(cli_dry, cfg)
    cap = int(cfg["submit"].get("daily_cap", 5))
    conn = connect()
    job = get_job(conn, job_id)
    if not job:
        raise SystemExit(
            f"Unknown job_id: {job_id}. "
            "GitHub's data/jobs.db does not have this row. "
            "After a local digest, commit and push data/jobs.db, then click Approve again."
        )
    if job["status"] != "pending_approval":
        print(f"Skip submit: {job_id} is {job['status']} (need pending_approval).")
        conn.close()
        return
    already = submitted_today(conn)
    if already >= cap:
        print(f"Daily cap reached ({already}/{cap}). Not submitting {job_id}.")
        conn.close()
        return

    message = _message_text(job)
    if not message:
        conn.close()
        raise SystemExit(f"No draft text for {job_id}.")
    print(f"{'DRY RUN' if dry else 'LIVE'} apply: {job['company']} — {job['role']}")

    try:
        with waas_context(headless=True) as context:
            page = context.new_page()
            page.goto(job["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            if _looks_external(page):
                raise RuntimeError("Listing looks like an external/company-site apply — skipped.")
            _click_apply(page)
            _fill_message(page, message)
            if dry:
                print("Dry-run: Apply clicked and note filled; Send not clicked.")
            else:
                _click_send(page)
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
