from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

load_dotenv()


def load_cookies() -> list[dict]:
    raw = os.environ.get("YC_SESSION_COOKIES", "").strip()
    if not raw:
        raise SystemExit(
            "YC_SESSION_COOKIES is not set. Run python scripts/export_session.py and "
            "store the base64 blob as a secret / in .env."
        )
    try:
        decoded = base64.b64decode(raw)
        cookies = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise SystemExit(f"YC_SESSION_COOKIES is not valid base64 JSON: {exc}") from exc
    if not isinstance(cookies, list) or not cookies:
        raise SystemExit("YC_SESSION_COOKIES must be a JSON array of Playwright cookies.")
    cleaned = []
    for cookie in cookies:
        item = dict(cookie)
        if "sameSite" in item and item["sameSite"] not in ("Strict", "Lax", "None"):
            item.pop("sameSite", None)
        cleaned.append(item)
    return cleaned


@contextmanager
def waas_context(headless: bool = True):
    cookies = load_cookies()
    playwright: Playwright | None = None
    browser: Browser | None = None
    context: BrowserContext | None = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        )
        context.add_cookies(cookies)
        yield context
    finally:
        if context:
            context.close()
        if browser:
            browser.close()
        if playwright:
            playwright.stop()
