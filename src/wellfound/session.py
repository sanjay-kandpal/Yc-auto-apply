from __future__ import annotations

import base64
import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from record_video import attach_page_tracker, finalize_recordings, recording_dir, recording_enabled, video_size
from wellfound.login import attempt_password_login, cookies_still_valid, load_credentials, notify_login_failed

load_dotenv()
log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def load_cookies() -> list[dict]:
    raw = os.environ.get("WELLFOUND_SESSION_COOKIES", "").strip()
    if not raw:
        return []
    try:
        decoded = base64.b64decode(raw)
        cookies = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise SystemExit(f"WELLFOUND_SESSION_COOKIES is not valid base64 JSON: {exc}") from exc
    if not isinstance(cookies, list):
        raise SystemExit("WELLFOUND_SESSION_COOKIES must be a JSON array of Playwright cookies.")
    cleaned = []
    for cookie in cookies:
        item = dict(cookie)
        if "sameSite" in item and item["sameSite"] not in ("Strict", "Lax", "None"):
            item.pop("sameSite", None)
        cleaned.append(item)
    return cleaned


def _new_context(browser: Browser, *, record_dir=None, storage_state=None) -> BrowserContext:
    kwargs: dict = {"user_agent": USER_AGENT}
    if storage_state is not None:
        kwargs["storage_state"] = storage_state
    if record_dir is not None:
        width, height = video_size()
        kwargs["record_video_dir"] = str(record_dir)
        kwargs["record_video_size"] = {"width": width, "height": height}
    return browser.new_context(**kwargs)


def _login_attachments(dest: Path, screenshot: Path) -> list[Path]:
    files: list[Path] = []
    if screenshot.is_file():
        files.append(screenshot)
    files.extend(sorted(dest.glob("clip-*.webm")))
    return files


def _drop_raw_videos(dest: Path) -> None:
    for path in dest.glob("*.webm"):
        if path.name.startswith("clip-"):
            continue
        try:
            path.unlink()
        except OSError:
            log.warning("Could not remove leftover login video %s", path)


def _password_login_with_retry(browser: Browser, context: BrowserContext) -> None:
    ok, err, extra = attempt_password_login(context)
    if ok:
        log.info("Logged in with Wellfound email/password (attempt 1).")
        return
    log.warning("Wellfound login attempt 1/2 failed: %s", err)

    dest = recording_dir()
    rec_ctx: BrowserContext | None = None
    pages: list = []
    screenshot = dest / "login-fail.png"
    last_err = err
    last_extra = extra
    succeeded = False
    try:
        rec_ctx = _new_context(browser, record_dir=dest)
        pages = attach_page_tracker(rec_ctx)
        ok, last_err, last_extra = attempt_password_login(rec_ctx, screenshot_path=screenshot)
        if ok:
            context.add_cookies(rec_ctx.cookies())
            succeeded = True
            log.info("Logged in with Wellfound email/password (attempt 2).")
    except Exception as exc:
        last_err = f"{type(exc).__name__}: {exc}"
        log.exception("Wellfound login attempt 2 crashed")
    finally:
        if rec_ctx:
            rec_ctx.close()

    if succeeded:
        _drop_raw_videos(dest)
        if screenshot.exists():
            screenshot.unlink(missing_ok=True)
        return

    try:
        finalize_recordings(pages, dest)
    except Exception:
        log.exception("Failed to finalize Wellfound login-failure recording")

    notify_login_failed(
        last_err,
        attachments=_login_attachments(dest, screenshot),
        extra=last_extra,
        after_retries=True,
    )
    raise SystemExit(f"Wellfound login failed after 2 attempts: {last_err}")


def _login(browser: Browser, context: BrowserContext) -> None:
    email, password = load_credentials()
    cookies = load_cookies()
    if not (email and password) and not cookies:
        notify_login_failed(
            "No credentials set. Add WELLFOUND_EMAIL and WELLFOUND_PASSWORD "
            "(GitHub secrets or credentials.local.yaml)."
        )
        raise SystemExit("No WELLFOUND_EMAIL/WELLFOUND_PASSWORD and no WELLFOUND_SESSION_COOKIES.")
    used_cookies = False
    if cookies:
        context.add_cookies(cookies)
        used_cookies = cookies_still_valid(context)
        if used_cookies:
            log.info("Using WELLFOUND_SESSION_COOKIES.")
        else:
            log.warning("Wellfound session cookies expired or invalid.")
    if used_cookies:
        return
    if email and password:
        _password_login_with_retry(browser, context)
        return
    notify_login_failed("Cookies failed and WELLFOUND_EMAIL / WELLFOUND_PASSWORD are not set.")
    raise SystemExit("Need valid cookies or WELLFOUND_EMAIL / WELLFOUND_PASSWORD.")


@contextmanager
def wellfound_context(headless: bool = True):
    playwright: Playwright | None = None
    browser: Browser | None = None
    context: BrowserContext | None = None
    pages: list = []
    dest_dir = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        context = _new_context(browser)
        _login(browser, context)
        if recording_enabled():
            dest_dir = recording_dir()
            state = context.storage_state()
            context.close()
            context = _new_context(browser, record_dir=dest_dir, storage_state=state)
            pages = attach_page_tracker(context)
            log.info("Spectate recording to %s", dest_dir)
        yield context
    finally:
        if context:
            context.close()
        if dest_dir:
            try:
                finalize_recordings(pages, dest_dir)
            except Exception:
                log.exception("Failed to finalize spectate recording")
        if browser:
            browser.close()
        if playwright:
            playwright.stop()
