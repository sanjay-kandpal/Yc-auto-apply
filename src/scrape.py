from __future__ import annotations

import logging
import random
import time

from config_loader import load_config
from db import connect, insert_discovered
from log_config import setup_logging
from session import waas_context
from waas_parse import jobs_from_inertia_html, jobs_from_json_text, job_url, parse_inertia_page

log = logging.getLogger(__name__)

INTERESTING = ("algolia.net", "companies/fetch", "workatastartup.com/companies")


def search_sources(search: dict) -> list[dict]:
    default_pages = int(search.get("max_pages", 20))
    raw = search.get("sources") or []
    sources = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        sources.append(
            {
                "name": str(item.get("name") or "default").strip() or "default",
                "url": url,
                "max_pages": int(item.get("max_pages", default_pages)),
            }
        )
    if sources:
        return sources
    url = str(search.get("url") or "").strip()
    if not url:
        raise SystemExit("Set search.sources or search.url in config.yaml")
    return [{"name": "default", "url": url, "max_pages": default_pages}]


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


def _enrich_from_job_page(page, job: dict, delay: list[float]) -> dict:
    page.goto(job["url"], wait_until="domcontentloaded", timeout=60000)
    html_text = page.content()
    parsed = parse_inertia_page(html_text)
    extras = jobs_from_inertia_html(html_text)
    for extra in extras:
        if extra["url"] == job["url"] and extra.get("jd_text"):
            job = {**job, **extra}
            break
    if parsed and not job.get("jd_text"):
        job["jd_text"] = str(parsed)[:8000]
    _delay(delay)
    return job


def _load_more(page, delay: list[float], collected: dict[str, dict], extra_pages: int) -> None:
    for _ in range(max(0, extra_pages)):
        more = page.locator(
            "button:has-text('Show more'), button:has-text('Load more'), "
            "a:has-text('Show more'), button:has-text('More')"
        )
        try:
            if more.count() and more.first.is_visible():
                more.first.click()
            else:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        _merge(collected, jobs_from_inertia_html(page.content()))
        _delay(delay)


def _collect_source(page, source: dict, delay: list[float], collected: dict[str, dict]) -> None:
    log.info("Scraping source %s", source["name"])
    page.goto(source["url"], wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)
    _merge(collected, jobs_from_inertia_html(page.content()))
    _load_more(page, delay, collected, int(source["max_pages"]) - 1)


def scrape() -> None:
    setup_logging()
    cfg = load_config()
    search = cfg["search"]
    delay = search.get("delay_seconds", [2, 6])
    sources = search_sources(search)
    collected: dict[str, dict] = {}

    with waas_context(headless=True) as context:
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
        from login import is_waas_logged_in, notify_login_failed

        if not is_waas_logged_in(page) and not collected:
            log.error("Login wall detected after auth")
            notify_login_failed(
                "Still seeing the Log In button after auth. Update YC_EMAIL / YC_PASSWORD."
            )
            raise SystemExit("Login wall detected. Update YC_EMAIL and YC_PASSWORD.")

        for source in sources[1:]:
            _collect_source(page, source, delay, collected)

        conn = connect()
        existing = {row["url"] for row in conn.execute("SELECT url FROM jobs")}
        new_jobs = [job for job in collected.values() if job["url"] not in existing]
        log.info("Discovered %s listings, %s new.", len(collected), len(new_jobs))

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
            job.setdefault("url", job_url(job.get("waas_id", "")))
            if insert_discovered(conn, job["company"], job["role"], job["url"], job.get("jd_text") or ""):
                inserted += 1
                log.debug("+ %s — %s %s", job["company"], job["role"], job["url"])
            else:
                log.debug("= dup %s — %s", job["company"], job["role"])
        conn.commit()
        conn.close()
        log.info("Inserted %s new jobs.", inserted)


if __name__ == "__main__":
    scrape()
