from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config_loader import load_config
from db import connect, insert_discovered
from log_config import setup_logging
from wellfound.filters import apply_job_filters
from wellfound.login import is_wellfound_logged_in, notify_login_failed
from wellfound.parse import jobs_from_html, jobs_from_json_text
from wellfound.session import wellfound_context

log = logging.getLogger(__name__)

# Narrow tokens only. Matching bare "wellfound.com" + response.text() in a sync
# page.on("response") handler deadlocks Playwright after View results.
INTERESTING = (
    "/graphql",
    "graphql?",
    "job_listings",
    "jobsearch",
    "job_search",
    "algolia.net",
    "algolia.io",
)

DEFAULT_JOBS_URL = "https://wellfound.com/jobs"
ENRICH_CAP = 25


def _delay(bounds: list[float]) -> None:
    lo, hi = float(bounds[0]), float(bounds[1])
    time.sleep(random.uniform(lo, hi))


def _merge(into: dict[str, dict], jobs: list[dict]) -> None:
    for job in jobs:
        key = job["url"]
        existing = into.get(key)
        if not existing:
            into[key] = job
            continue
        if len(job.get("jd_text") or "") > len(existing.get("jd_text") or ""):
            into[key] = job


def _is_interesting(url: str) -> bool:
    lower = url.lower()
    return any(token in lower for token in INTERESTING)


def _drain_responses(pending: list, collected: dict[str, dict]) -> int:
    """Read response bodies on the main thread (safe). Handler must only enqueue."""
    drained = 0
    while pending:
        response = pending.pop(0)
        try:
            text = response.text()
        except Exception:
            continue
        before = len(collected)
        _merge(collected, jobs_from_json_text(text))
        drained += 1
        if len(collected) > before:
            log.debug("JSON response added jobs (total %s)", len(collected))
    return drained


def _enrich_from_job_page(page, job: dict, delay: list[float]) -> dict:
    page.goto(job["url"], wait_until="domcontentloaded", timeout=60000)
    extras = jobs_from_html(page.content())
    for extra in extras:
        if extra["url"] == job["url"] and extra.get("jd_text"):
            job = {**job, **extra}
            break
        if extra.get("wellfound_id") and extra.get("wellfound_id") == job.get("wellfound_id"):
            if extra.get("jd_text"):
                job = {**job, **extra}
                break
    if not job.get("jd_text"):
        try:
            body = page.inner_text("body")
            job["jd_text"] = body[:8000]
        except Exception:
            pass
    _delay(delay)
    return job


def _load_more(
    page,
    delay: list[float],
    collected: dict[str, dict],
    extra_pages: int,
    pending: list,
) -> None:
    for i in range(max(0, extra_pages)):
        log.info("Load more / scroll %s/%s (have %s jobs)", i + 1, extra_pages, len(collected))
        more = page.locator(
            "button:has-text('Show more'), button:has-text('Load more'), "
            "a:has-text('Show more'), button:has-text('More'), "
            "button:has-text('See more')"
        )
        try:
            if more.count() and more.first.is_visible():
                more.first.click(timeout=5000)
            else:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
        _drain_responses(pending, collected)
        try:
            _merge(collected, jobs_from_html(page.content()))
        except Exception:
            log.exception("Failed to parse page HTML on scroll %s", i + 1)
        _delay(delay)


def _collect_filtered_results(
    page,
    search: dict,
    delay: list[float],
    collected: dict[str, dict],
    pending: list,
) -> None:
    jobs_url = str(search.get("jobs_url") or DEFAULT_JOBS_URL).strip() or DEFAULT_JOBS_URL
    max_pages = int(search.get("max_pages", 12))
    filters = search.get("filters") or {}

    log.info("Opening Wellfound jobs board %s", jobs_url)
    page.goto(jobs_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)

    if not is_wellfound_logged_in(page):
        log.error("Login wall on Wellfound jobs page")
        notify_login_failed(
            "Still seeing Log in on /jobs after auth. Update WELLFOUND_EMAIL / WELLFOUND_PASSWORD."
        )
        raise SystemExit("Wellfound login wall detected.")

    apply_job_filters(page, filters)
    log.info("Collecting results after View results…")
    page.wait_for_timeout(2000)
    _drain_responses(pending, collected)
    try:
        _merge(collected, jobs_from_html(page.content()))
    except Exception:
        log.exception("Failed to parse results HTML")
    log.info("After View results: %s jobs so far", len(collected))
    _load_more(page, delay, collected, max(0, max_pages - 1), pending)
    _drain_responses(pending, collected)


def scrape() -> None:
    setup_logging()
    cfg = load_config().get("wellfound") or {}
    search = cfg.get("search") or {}
    delay = search.get("delay_seconds", [2, 6])
    collected: dict[str, dict] = {}
    pending: list = []

    with wellfound_context(headless=True) as context:
        page = context.new_page()

        def on_response(response) -> None:
            # Do NOT call response.text() here — sync Playwright can deadlock.
            if not _is_interesting(response.url):
                return
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                return
            pending.append(response)

        page.on("response", on_response)
        _collect_filtered_results(page, search, delay, collected, pending)

        conn = connect()
        existing = {row["url"] for row in conn.execute("SELECT url FROM jobs")}
        new_jobs = [job for job in collected.values() if job["url"] not in existing]
        log.info("Discovered %s Wellfound listings, %s new.", len(collected), len(new_jobs))

        inserted = 0
        enriched = 0
        for job in new_jobs:
            if len(job.get("jd_text") or "") < 80:
                if enriched >= ENRICH_CAP:
                    log.warning(
                        "Enrich cap (%s) reached; skipping remaining short JDs this run",
                        ENRICH_CAP,
                    )
                    continue
                try:
                    log.info("Enrich %s — %s", job.get("company"), job.get("role"))
                    job = _enrich_from_job_page(page, job, delay)
                    enriched += 1
                except Exception as exc:
                    log.warning("Skip enrich %s: %s", job.get("url"), exc)
                    continue
            if not job.get("company") or not job.get("role"):
                continue
            if insert_discovered(
                conn,
                job["company"],
                job["role"],
                job["url"],
                job.get("jd_text") or "",
                source="wellfound",
            ):
                inserted += 1
                log.debug("+ %s — %s %s", job["company"], job["role"], job["url"])
            else:
                log.debug("= dup %s — %s", job["company"], job["role"])
        conn.commit()
        conn.close()
        log.info("Inserted %s new Wellfound jobs.", inserted)


if __name__ == "__main__":
    scrape()
