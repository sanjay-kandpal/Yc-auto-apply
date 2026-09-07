from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from login import cookies_still_valid, ensure_logged_in, load_credentials, notify_login_failed

load_dotenv()


def load_cookies() -> list[dict]:
    raw = os.environ.get("YC_SESSION_COOKIES", "").strip()
    if not raw:
        return []
    try:
        decoded = base64.b64decode(raw)
        cookies = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise SystemExit(f"YC_SESSION_COOKIES is not valid base64 JSON: {exc}") from exc
    if not isinstance(cookies, list):
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
    email, password = load_credentials()
    cookies = load_cookies()
    if not (email and password) and not cookies:
        notify_login_failed(
            "No credentials set. Add YC_EMAIL and YC_PASSWORD "
            "(GitHub secrets or credentials.local.yaml)."
        )
        raise SystemExit("No YC_EMAIL/YC_PASSWORD and no YC_SESSION_COOKIES.")

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
        used_cookies = False
        if cookies:
            context.add_cookies(cookies)
            used_cookies = cookies_still_valid(context)
            if used_cookies:
                print("Using YC_SESSION_COOKIES.")
            else:
                print("Session cookies expired or invalid.")
        if not used_cookies:
            if email and password:
                ensure_logged_in(context)
            else:
                notify_login_failed(
                    "Cookies failed and YC_EMAIL / YC_PASSWORD are not set."
                )
                raise SystemExit("Need valid cookies or YC_EMAIL / YC_PASSWORD.")
        yield context
    finally:
        if context:
            context.close()
        if browser:
            browser.close()
        if playwright:
            playwright.stop()
