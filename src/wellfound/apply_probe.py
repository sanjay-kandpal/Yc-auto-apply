from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from playwright.sync_api import Page

log = logging.getLogger(__name__)

APPLY_KINDS = (  # noqa: kept for docs / importers
    "cover_letter_only",
    "has_questions",
    "eligibility_blocked",
    "external_ats",
    "unknown",
)

EXTERNAL_TEXT_HINTS = (
    "apply on company",
    "apply on the company",
    "apply on their",
    "company website",
    "external application",
    "continue to apply",
    "apply via",
    "apply at",
)

ELIGIBILITY_HINTS = (
    "not eligible",
    "you are not eligible",
    "must be located",
    "must be based",
    "must live in",
    "only candidates in",
    "us work authorization",
    "u.s. work authorization",
    "authorized to work in the united states",
    "citizenship required",
    "this role is only available",
    "location requirement",
    "outside of our hiring locations",
)

COVER_FIELD_HINTS = (
    "cover letter",
    "coverletter",
    "message",
    "note",
    "why do you want",
    "tell us about yourself",
    "introduction",
    "introduce yourself",
)


@dataclass
class ProbeResult:
    apply_kind: str
    reason: str
    question_labels: list[str] = field(default_factory=list)

    def error_message(self) -> str:
        parts = [self.reason]
        if self.question_labels:
            labels = "; ".join(self.question_labels[:8])
            parts.append(f"Questions: {labels}")
        return " | ".join(parts)


def classify_external_from_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return False
    if "wellfound.com" in host or "angel.co" in host:
        return False
    # Left Wellfound (ATS host or any other domain).
    return True


def _body_text(page: Page) -> str:
    try:
        return page.inner_text("body").lower()
    except Exception:
        return ""


def looks_external_on_page(page: Page) -> bool:
    if classify_external_from_url(page.url):
        return True
    body = _body_text(page)
    return any(hint in body for hint in EXTERNAL_TEXT_HINTS)


def looks_eligibility_blocked(text: str) -> bool:
    blob = (text or "").lower()
    return any(hint in blob for hint in ELIGIBILITY_HINTS)


def _label_for(el) -> str:
    try:
        aria = (el.get_attribute("aria-label") or "").strip()
        if aria:
            return aria
        name = (el.get_attribute("name") or "").strip()
        if name:
            return name
        placeholder = (el.get_attribute("placeholder") or "").strip()
        if placeholder:
            return placeholder
        eid = (el.get_attribute("id") or "").strip()
        if eid:
            return eid
    except Exception:
        pass
    return ""


def _is_cover_like(label: str) -> bool:
    blob = (label or "").lower()
    if not blob:
        return True
    return any(hint in blob for hint in COVER_FIELD_HINTS)


def classify_form_snapshot(
    *,
    textareas: list[str],
    inputs: list[dict],
    page_text: str = "",
) -> ProbeResult:
    """Pure classifier used by the probe and unit tests (no browser)."""
    if looks_eligibility_blocked(page_text):
        return ProbeResult(
            apply_kind="eligibility_blocked",
            reason="Eligibility / location blocker text detected on apply UI.",
        )

    question_labels: list[str] = []
    cover_count = 0
    for label in textareas:
        if _is_cover_like(label):
            cover_count += 1
        else:
            question_labels.append(label or "textarea")

    for item in inputs:
        itype = (item.get("type") or "text").lower()
        label = item.get("label") or itype
        if itype in ("hidden", "submit", "button", "image", "reset", "file"):
            continue
        if itype in ("radio", "checkbox") or item.get("tag") in ("select", "radio", "checkbox"):
            question_labels.append(label)
            continue
        if itype in ("text", "email", "tel", "url", "number", "search") or item.get("tag") == "input":
            if _is_cover_like(label):
                cover_count += 1
            else:
                question_labels.append(label)

    if question_labels:
        return ProbeResult(
            apply_kind="has_questions",
            reason="Apply form has screening inputs beyond a single cover letter/note.",
            question_labels=question_labels,
        )

    if cover_count >= 1 or textareas:
        return ProbeResult(
            apply_kind="cover_letter_only",
            reason="Cover-letter/note form only.",
        )

    return ProbeResult(
        apply_kind="unknown",
        reason="Could not classify apply form fields.",
    )


def _collect_form_fields(page: Page) -> tuple[list[str], list[dict]]:
    textareas: list[str] = []
    for i in range(page.locator("textarea").count()):
        el = page.locator("textarea").nth(i)
        try:
            if not el.is_visible():
                continue
        except Exception:
            continue
        textareas.append(_label_for(el))

    inputs: list[dict] = []
    for i in range(page.locator("input").count()):
        el = page.locator("input").nth(i)
        try:
            if not el.is_visible():
                continue
            itype = (el.get_attribute("type") or "text").lower()
        except Exception:
            continue
        inputs.append({"tag": "input", "type": itype, "label": _label_for(el)})

    for i in range(page.locator("select").count()):
        el = page.locator("select").nth(i)
        try:
            if not el.is_visible():
                continue
        except Exception:
            continue
        inputs.append({"tag": "select", "type": "select", "label": _label_for(el)})

    # Role-based radios/checkboxes without classic input visibility edge cases.
    for role in ("radio", "checkbox"):
        loc = page.get_by_role(role)
        try:
            count = loc.count()
        except Exception:
            count = 0
        for i in range(count):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                name = (el.get_attribute("aria-label") or el.inner_text() or role).strip()
            except Exception:
                name = role
            inputs.append({"tag": role, "type": role, "label": name or role})

    return textareas, inputs


def _click_apply(page: Page) -> bool:
    from wellfound.apply_flow import open_apply_ui

    return open_apply_ui(page)


def probe_apply_form(page: Page, job_url: str) -> ProbeResult:
    """Open listing, open Apply UI if needed, classify. Never clicks Send."""
    page.goto(job_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)

    if looks_external_on_page(page):
        return ProbeResult(
            apply_kind="external_ats",
            reason=f"External / ATS apply detected ({page.url}).",
        )

    opened = _click_apply(page)
    page.wait_for_timeout(1000)

    if classify_external_from_url(page.url) or looks_external_on_page(page):
        return ProbeResult(
            apply_kind="external_ats",
            reason=f"Navigated off Wellfound or external apply UI ({page.url}).",
        )

    if looks_eligibility_blocked(_body_text(page)):
        return ProbeResult(
            apply_kind="eligibility_blocked",
            reason="Eligibility / location blocker text detected on apply UI.",
        )

    textareas, inputs = _collect_form_fields(page)
    if not textareas and not inputs and not opened:
        return ProbeResult(
            apply_kind="unknown",
            reason="Could not find Apply control or form fields.",
        )

    result = classify_form_snapshot(
        textareas=textareas,
        inputs=inputs,
        page_text=_body_text(page),
    )
    log.info("Apply probe classified as %s (%s)", result.apply_kind, result.reason)
    return result
