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
from scrape import search_sources
from wellfound.login import is_wellfound_logged_in, notify_login_failed
from wellfound.parse import jobs_from_html, jobs_from_json_text
from wellfound.session import wellfound_context

log = logging.getLogger(__name__)

INTERESTING = (
    "graphql",
    "wellfound.com",
    "angel.co",
    "job_listings",
    "jobSearch",
    "talent",
    "algolia",
)


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
    return any(token.lower() in lower for token in INTERESTING)


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


def _load_more(page, delay: list[float], collected: dict[str, dict], extra_pages: int) -> None:
    for _ in range(max(0, extra_pages)):
        more = page.locator(
            "button:has-text('Show more'), button:has-text('Load more'), "
            "a:has-text('Show more'), button:has-text('More'), "
            "button:has-text('See more')"
        )
        try:
            if more.count() and more.first.is_visible():
                more.first.click()
            else:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        _merge(collected, jobs_from_html(page.content()))
        _delay(delay)


def _collect_source(page, source: dict, delay: list[float], collected: dict[str, dict]) -> None:
    log.info("Scraping Wellfound source %s", source["name"])
    page.goto(source["url"], wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)
    _merge(collected, jobs_from_html(page.content()))
    _load_more(page, delay, collected, int(source["max_pages"]) - 1)


def scrape() -> None:
    setup_logging()
    cfg = load_config().get("wellfound") or {}
    search = cfg.get("search") or {}
    delay = search.get("delay_seconds", [2, 6])
    sources = search_sources(search)
    collected: dict[str, dict] = {}

    with wellfound_context(headless=True) as context:
        page = context.new_page()

        def on_response(response) -> None:
            url = response.url
            if not _is_interesting(url):
                return
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                return
            try:
                text = response.text()
            except Exception:
                return
            _merge(collected, jobs_from_json_text(text))

        page.on("response", on_response)
        _collect_source(page, sources[0], delay, collected)

        if not is_wellfound_logged_in(page) and not collected:
            log.error("Login wall detected after Wellfound auth")
            notify_login_failed(
                "Still seeing Log in after auth. Update WELLFOUND_EMAIL / WELLFOUND_PASSWORD."
            )
            raise SystemExit("Wellfound login wall detected.")

        for source in sources[1:]:
            _collect_source(page, source, delay, collected)

        conn = connect()
        existing = {row["url"] for row in conn.execute("SELECT url FROM jobs")}
        new_jobs = [job for job in collected.values() if job["url"] not in existing]
        log.info("Discovered %s Wellfound listings, %s new.", len(collected), len(new_jobs))

        inserted = 0
        for job in new_jobs:
            if len(job.get("jd_text") or "") < 80:
                try:
                    job = _enrich_from_job_page(page, job, delay)
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
