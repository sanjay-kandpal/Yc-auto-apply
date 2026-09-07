from __future__ import annotations

import html
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from playwright.sync_api import Page

from config_loader import ROOT, load_config
from mailer import try_send_html_email

load_dotenv()

CREDENTIALS_FILE = ROOT / "credentials.local.yaml"
PASSWORD_LOGIN_URL = (
    "https://account.ycombinator.com/?continue=https%3A%2F%2Fwww.workatastartup.com%2F"
)
PASSWORD_INSTEAD = (
    "a:has-text('username and password')",
    "a:has-text('Log in with username and password instead')",
)
USERNAME_SELECTORS = (
    "input[name='username'], input#username, input[type='email'], "
    "input[name='email'], input#email"
)
PASSWORD_SELECTORS = "input[type='password'], input[name='password'], input#password"
SUBMIT_SELECTORS = (
    "button[type='submit']:not(:has-text('Send login link'))",
    "input[type='submit']",
    "button:has-text('Log in')",
    "button:has-text('Sign in')",
)
OTP_HINTS = ("verification code", "one-time", "authenticator", "enter the code", "2fa", "two-factor")


def load_credentials(path: Path | None = None) -> tuple[str, str]:
    email = os.environ.get("YC_EMAIL", "").strip()
    password = os.environ.get("YC_PASSWORD", "").strip()
    cred_path = path or CREDENTIALS_FILE
    if cred_path.exists():
        data = yaml.safe_load(cred_path.read_text(encoding="utf-8")) or {}
        email = email or str(data.get("email") or "").strip()
        password = password or str(data.get("password") or "").strip()
    return email, password


def _first_visible(page: Page, selector: str):
    loc = page.locator(selector)
    if loc.count() == 0:
        return None
    try:
        if loc.first.is_visible():
            return loc.first
    except Exception:
        return loc.first
    return None


def _click_first(page: Page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        loc = page.locator(selector)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=4000)
                return True
        except Exception:
            continue
    return False


def _page_text(page: Page) -> str:
    try:
        return page.inner_text("body").lower()
    except Exception:
        return page.content().lower()


def is_waas_logged_in(page: Page) -> bool:
    """True only on workatastartup.com without a visible YC Log In link."""
    url = page.url.lower()
    if "account.ycombinator.com" in url or "workatastartup.com" not in url:
        return False
    login_link = page.locator("a[href*='account.ycombinator.com']")
    try:
        for i in range(min(login_link.count(), 5)):
            if login_link.nth(i).is_visible():
                return False
    except Exception:
        return False
    return True


def _blocked(page: Page) -> str | None:
    text = _page_text(page)
    if any(hint in text for hint in OTP_HINTS):
        return "2FA / verification code is required. Auto-login cannot complete."
    if "captcha" in text or "recaptcha" in text:
        return "CAPTCHA blocked automated login."
    return None


def perform_login(page: Page, email: str, password: str) -> tuple[bool, str]:
    cfg = load_config()
    start = cfg.get("login", {}).get("start_url", PASSWORD_LOGIN_URL)
    check = cfg.get("login", {}).get("check_url", "https://www.workatastartup.com/companies")

    page.goto(start, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)

    if "magic" in page.url.lower() or page.locator("#sign-in-card").count():
        if not _click_first(page, PASSWORD_INSTEAD):
            return False, "Landed on magic-link page and could not open username/password login."
        page.wait_for_timeout(2000)

    blocked = _blocked(page)
    if blocked:
        return False, blocked

    user_box = _first_visible(page, USERNAME_SELECTORS)
    password_box = _first_visible(page, PASSWORD_SELECTORS)
    if not user_box or not password_box:
        return False, "YC username/password fields not found. Open account.ycombinator.com/?continue=..."
    user_box.fill(email)
    password_box.fill(password)
    if not _click_first(page, SUBMIT_SELECTORS):
        password_box.press("Enter")

    try:
        page.wait_for_url("**workatastartup.com**", timeout=30000)
    except Exception:
        page.wait_for_timeout(4000)

    blocked = _blocked(page)
    if blocked:
        return False, blocked

    page.goto(check, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    if is_waas_logged_in(page):
        return True, "ok"
    return False, "YC login did not reach Work at a Startup. Check YC_EMAIL / YC_PASSWORD."


def cookies_still_valid(context) -> bool:
    cfg = load_config()
    check = cfg.get("login", {}).get("check_url", "https://www.workatastartup.com/companies")
    page = context.new_page()
    try:
        page.goto(check, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        return is_waas_logged_in(page)
    except Exception:
        return False
    finally:
        page.close()


def notify_login_failed(reason: str) -> None:
    cfg = load_config()
    owner = cfg.get("github", {}).get("owner", "YOUR_GITHUB_USER")
    repo = cfg.get("github", {}).get("repo", "Yc-auto-apply")
    actions = f"https://github.com/{owner}/{repo}/actions/workflows/scan.yml"
    body = f"""
    <p>Work at a Startup <strong>login failed</strong>. The 4-hour scan did not scrape jobs.</p>
    <p><strong>Reason:</strong> {html.escape(reason)}</p>
    <p>Use your <em>YC account</em> username/password (not the magic-link page). Then re-run scan.</p>
    <p><a href="{html.escape(actions)}">Open the scan workflow</a></p>
    """
    sent = try_send_html_email("YC job bot: login failed — update email/password", body)
    if sent:
        print("Sent login-failure email.")
    else:
        print("Login failed and no failure email could be sent. Set GMAIL_ADDRESS / GMAIL_APP_PASSWORD.")


def ensure_logged_in(context) -> None:
    email, password = load_credentials()
    if not email or not password:
        notify_login_failed("YC_EMAIL / YC_PASSWORD are empty.")
        raise SystemExit("YC_EMAIL / YC_PASSWORD are empty.")
    page = context.new_page()
    try:
        ok, err = perform_login(page, email, password)
    finally:
        page.close()
    if not ok:
        notify_login_failed(err)
        raise SystemExit(f"Login failed: {err}")
    print("Logged in with YC username/password.")


def main() -> None:
    from session import waas_context

    with waas_context(headless=True) as context:
        page = context.new_page()
        page.goto("https://www.workatastartup.com/companies", wait_until="domcontentloaded")
        print(f"Login check URL: {page.url}")
        print(f"Logged in: {is_waas_logged_in(page)}")


if __name__ == "__main__":
    main()
