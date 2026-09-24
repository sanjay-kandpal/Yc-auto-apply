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


def _wait_visible(page: Page, selector: str, timeout: int = 8000):
    loc = page.locator(selector).first
    try:
        loc.wait_for(state="visible", timeout=timeout)
        return loc
    except Exception:
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


def _page_extra(page: Page) -> str:
    url = ""
    title = ""
    try:
        url = page.url
    except Exception:
        pass
    try:
        title = page.title()
    except Exception:
        pass
    parts = [p for p in (url, title) if p]
    return "\n".join(parts)


def _save_screenshot(page: Page, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        log.exception("Could not save Wellfound login screenshot to %s", path)


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

    user_box = _wait_visible(page, USERNAME_SELECTORS)
    password_box = _wait_visible(page, PASSWORD_SELECTORS)
    if not user_box or not password_box:
        return False, "Wellfound email/password fields not found. Try WELLFOUND_SESSION_COOKIES."
    user_box.fill(email)
    password_box.fill(password)
    if not _click_first(page, SUBMIT_SELECTORS):
        try:
            password_box.press("Enter", timeout=5000)
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                return False, "Could not submit Wellfound login form."

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


def attempt_password_login(
    context,
    *,
    screenshot_path: Path | None = None,
) -> tuple[bool, str, str]:
    email, password = load_credentials()
    if not email or not password:
        return False, "WELLFOUND_EMAIL / WELLFOUND_PASSWORD are empty.", ""
    page = context.new_page()
    extra = ""
    try:
        ok, err = perform_login(page, email, password)
        extra = _page_extra(page)
        if not ok and screenshot_path:
            _save_screenshot(page, screenshot_path)
        return ok, err, extra
    except Exception as exc:
        extra = _page_extra(page)
        if screenshot_path:
            _save_screenshot(page, screenshot_path)
        log.exception("Wellfound password login raised")
        return False, f"{type(exc).__name__}: {exc}", extra
    finally:
        page.close()


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


def notify_login_failed(
    reason: str,
    *,
    attachments: list[Path | str] | None = None,
    extra: str = "",
    after_retries: bool = False,
) -> None:
    cfg = load_config()
    owner = cfg.get("github", {}).get("owner", "YOUR_GITHUB_USER")
    repo = cfg.get("github", {}).get("repo", "Yc-auto-apply")
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if run_id:
        actions = f"https://github.com/{owner}/{repo}/actions/runs/{run_id}"
        actions_label = "Open this Actions run"
    else:
        actions = f"https://github.com/{owner}/{repo}/actions/workflows/scan-wellfound.yml"
        actions_label = "Open the Wellfound scan workflow"
    if after_retries:
        subject = "Wellfound job bot: login failed after 2 attempts"
        headline = (
            "Wellfound <strong>login failed after 2 attempts</strong>. "
            "The scan did not scrape jobs."
        )
        evidence = (
            "<p>A screenshot and recording clip are attached when available. "
            "The scan recording (if Actions finished spectate) is on the same run.</p>"
        )
    else:
        subject = "Wellfound job bot: login failed — update email/password"
        headline = "Wellfound <strong>login failed</strong>. The scan did not scrape jobs."
        evidence = ""
    extra_html = f"<p>{html.escape(extra)}</p>" if extra else ""
    body = f"""
    <p>{headline}</p>
    <p><strong>Reason:</strong> {html.escape(reason)}</p>
    {extra_html}
    {evidence}
    <p>Set <em>WELLFOUND_EMAIL</em> / <em>WELLFOUND_PASSWORD</em> or cookie fallback, then re-run scan-wellfound.</p>
    <p><a href="{html.escape(actions)}">{html.escape(actions_label)}</a></p>
    """
    sent = try_send_html_email(subject, body, attachments=attachments)
    if sent:
        log.info("Sent Wellfound login-failure email.")
    else:
        log.warning("Wellfound login failed and no failure email could be sent.")


def ensure_logged_in(context) -> None:
    email, password = load_credentials()
    if not email or not password:
        notify_login_failed("WELLFOUND_EMAIL / WELLFOUND_PASSWORD are empty.")
        raise SystemExit("WELLFOUND_EMAIL / WELLFOUND_PASSWORD are empty.")
    ok, err, extra = attempt_password_login(context)
    if not ok:
        notify_login_failed(err, extra=extra)
        raise SystemExit(f"Wellfound login failed: {err}")
    log.info("Logged in with Wellfound email/password.")
