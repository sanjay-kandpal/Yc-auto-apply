from __future__ import annotations

import html
import logging
import os
from pathlib import Path

import yaml
from playwright.sync_api import Page

from config_loader import ROOT, load_config
from mailer import try_send_html_email

log = logging.getLogger(__name__)

CREDENTIALS_FILE = ROOT / "credentials.local.yaml"
PASSWORD_LOGIN_URL = "https://wellfound.com/login"
USERNAME_SELECTORS = (
    "input[name='email'], input#email, input[type='email'], "
    "input[name='username'], input#username, input[autocomplete='email']"
)
PASSWORD_SELECTORS = (
    "input[type='password'], input[name='password'], input#password, "
    "input[autocomplete='current-password']"
)
SUBMIT_SELECTORS = (
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Log in')",
    "button:has-text('Sign in')",
    "button:has-text('Continue')",
)
OTP_HINTS = ("verification code", "one-time", "authenticator", "enter the code", "2fa", "two-factor")


def load_credentials(path: Path | None = None) -> tuple[str, str]:
    email = os.environ.get("WELLFOUND_EMAIL", "").strip()
    password = os.environ.get("WELLFOUND_PASSWORD", "").strip()
    cred_path = path or CREDENTIALS_FILE
    if cred_path.exists():
        data = yaml.safe_load(cred_path.read_text(encoding="utf-8")) or {}
        nested = data.get("wellfound") if isinstance(data.get("wellfound"), dict) else {}
        email = (
            email
            or str(data.get("wellfound_email") or "").strip()
            or str(nested.get("email") or "").strip()
        )
        password = (
            password
            or str(data.get("wellfound_password") or "").strip()
            or str(nested.get("password") or "").strip()
        )
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


def is_wellfound_logged_in(page: Page) -> bool:
    url = page.url.lower()
    if "wellfound.com" not in url and "angel.co" not in url:
        return False
    if any(part in url for part in ("/login", "/signup", "/users/sign_in")):
        return False
    login_link = page.get_by_role("link", name="Log in")
    try:
        if login_link.count() and login_link.first.is_visible():
            return False
    except Exception:
        pass
    text = _page_text(page)
    if "log in" in text and "sign up" in text and "jobs" not in url:
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
    cfg = load_config().get("wellfound") or {}
    start = (cfg.get("login") or {}).get("start_url", PASSWORD_LOGIN_URL)
    check = (cfg.get("login") or {}).get("check_url", "https://wellfound.com/jobs")

    page.goto(start, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)

    blocked = _blocked(page)
    if blocked:
        return False, blocked

    user_box = _first_visible(page, USERNAME_SELECTORS)
    password_box = _first_visible(page, PASSWORD_SELECTORS)
    if not user_box or not password_box:
        return False, "Wellfound email/password fields not found. Try WELLFOUND_SESSION_COOKIES."
    user_box.fill(email)
    password_box.fill(password)
    if not _click_first(page, SUBMIT_SELECTORS):
        password_box.press("Enter")

    try:
        page.wait_for_url("**wellfound.com/**", timeout=30000)
    except Exception:
        page.wait_for_timeout(4000)

    blocked = _blocked(page)
    if blocked:
        return False, blocked

    page.goto(check, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    if is_wellfound_logged_in(page):
        return True, "ok"
    return False, "Wellfound login did not reach the jobs page. Check WELLFOUND_EMAIL / WELLFOUND_PASSWORD."


def cookies_still_valid(context) -> bool:
    cfg = load_config().get("wellfound") or {}
    check = (cfg.get("login") or {}).get("check_url", "https://wellfound.com/jobs")
    page = context.new_page()
    try:
        page.goto(check, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        return is_wellfound_logged_in(page)
    except Exception:
        return False
    finally:
        page.close()


def notify_login_failed(reason: str) -> None:
    cfg = load_config()
    owner = cfg.get("github", {}).get("owner", "YOUR_GITHUB_USER")
    repo = cfg.get("github", {}).get("repo", "Yc-auto-apply")
    actions = f"https://github.com/{owner}/{repo}/actions/workflows/scan-wellfound.yml"
    body = f"""
    <p>Wellfound <strong>login failed</strong>. The scan did not scrape jobs.</p>
    <p><strong>Reason:</strong> {html.escape(reason)}</p>
    <p>Set <em>WELLFOUND_EMAIL</em> / <em>WELLFOUND_PASSWORD</em> or cookie fallback, then re-run scan-wellfound.</p>
    <p><a href="{html.escape(actions)}">Open the Wellfound scan workflow</a></p>
    """
    sent = try_send_html_email("Wellfound job bot: login failed — update email/password", body)
    if sent:
        log.info("Sent Wellfound login-failure email.")
    else:
        log.warning("Wellfound login failed and no failure email could be sent.")


def ensure_logged_in(context) -> None:
    email, password = load_credentials()
    if not email or not password:
        notify_login_failed("WELLFOUND_EMAIL / WELLFOUND_PASSWORD are empty.")
        raise SystemExit("WELLFOUND_EMAIL / WELLFOUND_PASSWORD are empty.")
    page = context.new_page()
    try:
        ok, err = perform_login(page, email, password)
    finally:
        page.close()
    if not ok:
        notify_login_failed(err)
        raise SystemExit(f"Wellfound login failed: {err}")
    log.info("Logged in with Wellfound email/password.")
