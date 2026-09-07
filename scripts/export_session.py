from __future__ import annotations

import base64
import json

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://www.workatastartup.com"


def main() -> None:
    print("A browser window will open. Log in to Work at a Startup, then return here.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        input("Press Enter after you are logged in and can see jobs...")
        cookies = context.cookies()
        browser.close()
    if not cookies:
        raise SystemExit("No cookies captured.")
    blob = base64.b64encode(json.dumps(cookies).encode("utf-8")).decode("ascii")
    print("\nYC_SESSION_COOKIES value:\n")
    print(blob)
    print("\nPaste that into GitHub Actions secrets and/or your local .env")
    print("Do not commit it. Session cookies expire — re-run this script when scrape hits a login wall.")


if __name__ == "__main__":
    main()
