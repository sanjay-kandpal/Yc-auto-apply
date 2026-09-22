from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page

from wellfound.apply_probe import (
    ProbeResult,
    classify_external_from_url,
    classify_form_snapshot,
    looks_eligibility_blocked,
    looks_external_on_page,
    _collect_form_fields,
)

log = logging.getLogger(__name__)

CONFIRM_HINTS = (
    "applied",
    "application",
    "thank you",
    "thanks for applying",
    "we've received",
    "we have received",
    "sent",
)


@dataclass
class ApplyOutcome:
    ok: bool
    apply_kind: str
    reason: str
    confirmation_signal: str = ""
    jd_text: str = ""
    dry_run: bool = False


def confirmation_payload(*, ok: bool, url: str = "", text: str = "") -> str:
    snippet = re.sub(r"\s+", " ", (text or "").strip())[:240]
    return json.dumps({"ok": bool(ok), "url": url or "", "text": snippet}, ensure_ascii=False)


def extract_job_description_text(html_or_text: str) -> str:
    """Pull JD text from a fixture or page fragment (pure helper for tests)."""
    raw = html_or_text or ""
    match = re.search(
        r'id=["\']job-description["\'][^>]*>(.*?)</div>',
        raw,
        re.I | re.S,
    )
    if match:
        fragment = match.group(1)
        text = re.sub(r"<[^>]+>", " ", fragment)
        return re.sub(r"\s+", " ", text).strip()
    if "About the job" in raw:
        after = raw.split("About the job", 1)[1]
        text = re.sub(r"<[^>]+>", " ", after)
        return re.sub(r"\s+", " ", text).strip()[:8000]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def read_modal_jd(page: Page) -> str:
    """Read JD from public #job-description, About the job block, or logged-in JobDetail."""
    loc = page.locator("#job-description")
    try:
        if loc.count() and loc.first.is_visible():
            text = (loc.first.inner_text() or "").strip()
            if text:
                return text
    except Exception:
        pass

    about = page.get_by_role("heading", name=re.compile(r"about the job", re.I))
    try:
        if about.count():
            container = about.first.locator("xpath=ancestor::div[contains(@class,'rounded')][1]")
            if container.count():
                text = (container.first.inner_text() or "").strip()
                if text:
                    return text
    except Exception:
        pass

    # Logged-in candidate job view: full description lives under JobDetail (no #job-description).
    detail = page.locator('[data-test="JobDetail"]')
    try:
        if detail.count() and detail.first.is_visible():
            raw = (detail.first.inner_text() or "").strip()
            raw = re.split(r"\n\s*Similar jobs\b", raw, maxsplit=1, flags=re.I)[0].strip()
            if len(raw) >= 80:
                return raw
    except Exception:
        pass
    return ""


def click_learn_more(page: Page) -> bool:
    """Legacy feed/modal opener. Returns True if clicked; False if absent."""
    candidates = (
        page.locator('button[data-test="LearnMoreButton"]'),
        page.get_by_role("button", name=re.compile(r"^\s*learn more\s*$", re.I)),
        page.locator("button", has_text=re.compile(r"^\s*learn more\s*$", re.I)),
        page.get_by_role("link", name=re.compile(r"^\s*learn more\s*$", re.I)),
    )
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=8000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def ensure_job_details(page: Page) -> None:
    """Ensure JD is readable. Logged-in pages already show [data-test=JobDetail]."""
    if read_modal_jd(page).strip():
        return
    if click_learn_more(page):
        page.wait_for_timeout(1000)
        if read_modal_jd(page).strip():
            return
    raise RuntimeError(
        "Could not find job description "
        "(no #job-description / JobDetail / About the job, and no Learn more)."
    )


APPLY_NOW_NAME = re.compile(
    r"^[\s\u00a0]*(?:easy[\s\u00a0]+apply|apply[\s\u00a0]+now)\b",
    re.I,
)
APPLY_NAME = re.compile(r"^[\s\u00a0]*apply(?!ied)\b", re.I)
_APPLIED_NAME = re.compile(r"^[\s\u00a0]*applied\b", re.I)
_JOB_SLUG = re.compile(r"/jobs/([^/?#]+)")
_TARGET_JOB = re.compile(r"/jobs/([^/'\"?\s#]+)")


def is_apply_now_name(name: str) -> bool:
    """True for Apply now / Easy Apply, including a short suffix after the label."""
    return bool(APPLY_NOW_NAME.match(re.sub(r"\s+", " ", (name or "").strip())))


def is_apply_name(name: str) -> bool:
    """True for Apply, Apply now, Easy Apply, or Apply plus extra words. Not Applied."""
    text = re.sub(r"\s+", " ", (name or "").strip())
    if _APPLIED_NAME.match(text):
        return False
    return bool(APPLY_NAME.match(text) or is_apply_now_name(text))


def job_slug_from_url(url: str) -> str:
    match = _JOB_SLUG.search(urlsplit(url or "").path)
    if not match:
        return ""
    slug = match.group(1)
    if slug in {"signup", "login"}:
        return ""
    return slug


def targets_other_job(target: str, slug: str) -> bool:
    """True when a control's href/onclick points at a different Wellfound job."""
    if not target or not slug:
        return False
    found = _TARGET_JOB.findall(target)
    if not found:
        return False
    return not any(item == slug for item in found)


def with_auto_open_query(url: str) -> str | None:
    """Job URL plus autoOpenApplication=true. None if already set or not a job page."""
    parts = urlsplit(url or "")
    host = (parts.netloc or "").lower()
    if "wellfound.com" not in host and not host.endswith("angel.co"):
        return None
    if not job_slug_from_url(url):
        return None
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if query.get("autoOpenApplication") == "true":
        return None
    query["autoOpenApplication"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _control_target(el) -> str:
    parts = []
    for attr in ("href", "onclick", "formaction"):
        try:
            value = el.get_attribute(attr) or ""
        except Exception:
            value = ""
        if value:
            parts.append(value)
    return " ".join(parts)


def _click_visible(locator, *, slug: str, timeout: int = 8000) -> bool:
    try:
        count = locator.count()
    except Exception:
        return False
    for i in range(min(count, 12)):
        el = locator.nth(i)
        try:
            if not el.is_visible():
                continue
            if targets_other_job(_control_target(el), slug):
                continue
            el.scroll_into_view_if_needed(timeout=2000)
            el.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _apply_locators(scope):
    now_text = re.compile(
        r"^[\s\u00a0]*(?:easy[\s\u00a0]+apply|apply[\s\u00a0]+now)[\s\u00a0]*$",
        re.I,
    )
    return (
        scope.get_by_role("button", name=APPLY_NOW_NAME),
        scope.get_by_role("link", name=APPLY_NOW_NAME),
        scope.locator("button, a", has_text=now_text),
        scope.locator('a[href*="autoOpenApplication"], button[onclick*="autoOpenApplication"]'),
        scope.get_by_role("button", name=APPLY_NAME),
        scope.get_by_role("link", name=APPLY_NAME),
        scope.locator('button[data-test="Button"]', has_text=APPLY_NAME),
    )


def _apply_ui_visible(page: Page) -> bool:
    selectors = (
        '[data-test="JobApplication-Modal"]',
        '[data-test="JobApplicationModal--SubmitButton"]',
        'textarea[name^="customQuestionAnswers"]',
    )
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _click_apply_control(page: Page) -> bool:
    slug = job_slug_from_url(page.url)
    scopes = []
    for scope_sel in ('[data-test="JobDetail"]', '[data-test="JobListing"]'):
        scope = page.locator(scope_sel)
        try:
            if scope.count():
                scopes.append(scope)
        except Exception:
            continue
    scopes.append(page)
    for scope in scopes:
        for loc in _apply_locators(scope):
            if _click_visible(loc, slug=slug):
                return True
    return False


def _goto_auto_open(page: Page) -> bool:
    target = with_auto_open_query(page.url)
    if not target:
        return False
    page.goto(target, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    return True


def open_apply_ui(page: Page) -> bool:
    """Open the apply form. Skips hidden duplicates and other jobs' Apply buttons."""
    if _apply_ui_visible(page):
        return True
    if _click_apply_control(page):
        page.wait_for_timeout(1500)
        return True
    if not _goto_auto_open(page):
        return False
    if _apply_ui_visible(page) or _click_apply_control(page):
        page.wait_for_timeout(1500)
        return True
    return False


def click_dialog_apply(page: Page) -> None:
    """Open apply form. Prefer Apply now inside JobDetail (logged-in) or JobListing."""
    if open_apply_ui(page):
        return
    raise RuntimeError("Could not find Apply / Apply Now button.")


def _answer_textarea(page: Page):
    specific = page.locator('textarea[name^="customQuestionAnswers"][name$="[answer]"]')
    if specific.count():
        for i in range(specific.count()):
            el = specific.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                continue
    boxes = page.locator("textarea")
    for i in range(boxes.count()):
        el = boxes.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:
            continue
    return None


def classify_modal_form(page: Page) -> ProbeResult:
    if looks_eligibility_blocked(page.inner_text("body")):
        return ProbeResult(
            apply_kind="eligibility_blocked",
            reason="Eligibility / location blocker text detected on apply UI.",
        )
    textareas, inputs = _collect_form_fields(page)
    # Treat customQuestionAnswers textareas as cover-like even if name looks opaque.
    normalized = []
    for label in textareas:
        blob = (label or "").lower()
        if "customquestionanswers" in blob or blob.endswith("[answer]"):
            normalized.append("cover letter")
        else:
            normalized.append(label)
    return classify_form_snapshot(
        textareas=normalized,
        inputs=inputs,
        page_text=page.inner_text("body"),
    )


def set_textarea_value(page: Page, box, text: str) -> None:
    box.wait_for(state="visible", timeout=15000)
    box.click(timeout=5000)
    box.fill("")
    box.evaluate(
        """(el, value) => {
            const desc = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, "value"
            );
            if (desc && desc.set) {
                desc.set.call(el, value);
            } else {
                el.value = value;
            }
            el.dispatchEvent(new InputEvent("input", {
                bubbles: true, cancelable: true, inputType: "insertText", data: value,
            }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
        }""",
        text,
    )


def send_application_button(page: Page):
    specific = page.locator('button[data-test="JobApplicationModal--SubmitButton"]')
    if specific.count():
        return specific.first
    return page.get_by_role("button", name=re.compile(r"send application", re.I)).first


def send_enabled(page: Page) -> bool:
    btn = send_application_button(page)
    try:
        return btn.is_visible() and btn.is_enabled()
    except Exception:
        return False


def fill_answer(page: Page, text: str) -> None:
    box = _answer_textarea(page)
    if box is None:
        raise RuntimeError("Could not find application answer textarea.")
    set_textarea_value(page, box, text)
    if send_enabled(page):
        return
    log.warning("Send application still disabled after fill; typing the note.")
    box.click(timeout=5000)
    box.fill("")
    box.press_sequentially(text, delay=8)
    if not send_enabled(page):
        raise RuntimeError("Answer filled but Send application stayed disabled.")


def click_send_application(page: Page) -> None:
    btn = send_application_button(page)
    btn.wait_for(state="visible", timeout=15000)
    page.wait_for_function(
        """() => {
            const byTest = document.querySelector(
              'button[data-test="JobApplicationModal--SubmitButton"]'
            );
            if (byTest && !byTest.disabled) return true;
            const buttons = [...document.querySelectorAll('button')];
            const send = buttons.find((b) =>
              /send application/i.test((b.textContent || '').trim())
            );
            return Boolean(send && !send.disabled);
        }""",
        timeout=20000,
    )
    btn.click()


def capture_confirmation(page: Page) -> str:
    url = page.url
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    blob = (body or "").lower()
    ok = any(hint in blob for hint in CONFIRM_HINTS)
    return confirmation_payload(ok=ok, url=url, text=body)


def open_job_and_read_jd(page: Page, job_url: str) -> str:
    page.goto(job_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    if classify_external_from_url(page.url) or looks_external_on_page(page):
        raise ApplyFlowError(
            "external_ats",
            f"External / ATS apply detected ({page.url}).",
        )
    try:
        ensure_job_details(page)
    except RuntimeError as exc:
        raise ApplyFlowError("unknown", str(exc)) from exc
    if classify_external_from_url(page.url) or looks_external_on_page(page):
        raise ApplyFlowError(
            "external_ats",
            f"Navigated off Wellfound after opening job details ({page.url}).",
        )
    jd = read_modal_jd(page)
    if not jd.strip():
        raise ApplyFlowError("unknown", "Could not read About the job description.")
    return jd


class ApplyFlowError(Exception):
    def __init__(self, apply_kind: str, reason: str):
        super().__init__(reason)
        self.apply_kind = apply_kind
        self.reason = reason


def run_apply_after_draft(
    page: Page,
    message: str,
    *,
    dry_run: bool = False,
) -> ApplyOutcome:
    """Click Apply, validate single-answer form, fill, optionally Send."""
    click_dialog_apply(page)
    page.wait_for_timeout(1000)

    if classify_external_from_url(page.url) or looks_external_on_page(page):
        return ApplyOutcome(
            ok=False,
            apply_kind="external_ats",
            reason=f"External / ATS after Apply ({page.url}).",
            confirmation_signal=confirmation_payload(ok=False, url=page.url, text="external"),
        )

    classified = classify_modal_form(page)
    if classified.apply_kind != "cover_letter_only":
        return ApplyOutcome(
            ok=False,
            apply_kind=classified.apply_kind,
            reason=classified.error_message(),
            confirmation_signal=confirmation_payload(
                ok=False, url=page.url, text=classified.error_message()
            ),
        )

    fill_answer(page, message)
    if dry_run:
        signal = confirmation_payload(ok=False, url=page.url, text="dry-run: Send not clicked")
        return ApplyOutcome(
            ok=True,
            apply_kind="cover_letter_only",
            reason="Dry-run: filled answer; Send application not clicked.",
            confirmation_signal=signal,
            dry_run=True,
        )

    click_send_application(page)
    page.wait_for_timeout(3000)
    signal = capture_confirmation(page)
    return ApplyOutcome(
        ok=True,
        apply_kind="cover_letter_only",
        reason="Send application clicked.",
        confirmation_signal=signal,
    )
