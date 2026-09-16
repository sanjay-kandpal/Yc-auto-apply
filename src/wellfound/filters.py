from __future__ import annotations

import logging
import re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)


def _click_filters_button(page: Page) -> None:
    candidates = (
        page.locator("button, [role='button'], a").filter(has_text=re.compile(r"^\s*Filters\s*$")),
        page.locator("svg.styles_filtersIcon__WhlNp").locator("xpath=ancestor::*[self::button or @role='button'][1]"),
        page.get_by_text("Filters", exact=True),
    )
    last_err: Exception | None = None
    for loc in candidates:
        try:
            target = loc.first
            target.wait_for(state="visible", timeout=8000)
            target.click(timeout=5000)
            page.wait_for_timeout(800)
            return
        except Exception as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Could not open Wellfound Filters panel: {last_err}")


def _ensure_full_time(page: Page) -> None:
    box = page.locator("#form-input--jobTypes--full_time")
    box.wait_for(state="attached", timeout=15000)
    if not box.is_checked():
        label = page.locator("label[for='form-input--jobTypes--full_time']")
        if label.count():
            label.first.click(timeout=5000)
        else:
            box.check(force=True)
        page.wait_for_timeout(300)
    log.info("Filter: Full Time selected")


def _role_chip_text(page: Page) -> str:
    chip = page.locator("span.styles_label__ikMuI")
    if chip.count():
        try:
            return (chip.first.inner_text() or "").strip()
        except Exception:
            pass
    btn = page.locator("[data-test='SearchBar-RoleSelect-FocusButton']")
    if btn.count():
        try:
            return (btn.first.inner_text() or "").strip()
        except Exception:
            pass
    return ""


def _role_already_selected(page: Page, role: str) -> bool:
    text = _role_chip_text(page).lower()
    return bool(text) and role.lower() in text


def _all_roles_selected(page: Page, roles: list[str]) -> bool:
    if not roles:
        return True
    text = _role_chip_text(page).lower()
    if not text:
        return False
    return all(role.lower() in text for role in roles)


def _open_role_picker(page: Page) -> None:
    # Prefer the stable focus button from the Filters modal (react-select wrapper).
    focus = page.locator("[data-test='SearchBar-RoleSelect-FocusButton']")
    if focus.count():
        focus.first.click(timeout=8000, force=True)
        page.wait_for_timeout(400)
        return
    container = page.locator(".select__value-container, .select__control").first
    try:
        container.click(timeout=5000, force=True)
        page.wait_for_timeout(400)
    except Exception as exc:
        raise RuntimeError(f"Could not open Wellfound role picker: {exc}") from exc


def _select_role(page: Page, role: str) -> None:
    if _role_already_selected(page, role):
        log.info("Filter: role already selected — %s", role)
        return

    _open_role_picker(page)

    # react-select: type into the visible input inside the role control.
    inputs = page.locator(
        "[data-test='SearchBar-RoleSelect-FocusButton'] input, "
        ".select__input input, input[id*='react-select'], "
        "input[type='text'], input[type='search']"
    )
    typed = False
    for i in range(min(inputs.count(), 10)):
        box = inputs.nth(i)
        try:
            if not box.is_visible():
                continue
            box.click(timeout=3000, force=True)
            box.fill("")
            box.type(role, delay=40)
            typed = True
            page.wait_for_timeout(700)
            break
        except Exception:
            continue

    # Pick matching menu option (prefer exact role label).
    option = page.locator(
        ".select__option, [id*='react-select'][id*='option'], [role='option']"
    ).filter(has_text=re.compile(rf"^{re.escape(role)}$", re.I))
    if not option.count():
        option = page.get_by_text(role, exact=True)
    if option.count():
        for i in range(min(option.count(), 8)):
            try:
                el = option.nth(i)
                if el.is_visible():
                    el.click(timeout=4000, force=True)
                    page.wait_for_timeout(400)
                    log.info("Filter: selected role %s", role)
                    return
            except Exception:
                continue

    if typed and _role_already_selected(page, role):
        log.info("Filter: selected role via type — %s", role)
        return

    raise RuntimeError(f"Could not select Wellfound role: {role}")


def _ensure_roles(page: Page, roles: list[str]) -> None:
    if not roles:
        return
    if _all_roles_selected(page, roles):
        log.info("Filter: roles already set — %s", _role_chip_text(page))
        return
    for role in roles:
        if _role_already_selected(page, role):
            log.info("Filter: role already selected — %s", role)
            continue
        _select_role(page, role)
    if not _all_roles_selected(page, roles):
        missing = [r for r in roles if not _role_already_selected(page, r)]
        raise RuntimeError(f"Roles not fully set after selection. Missing: {missing}")


def _set_experience_years(page: Page, low: int, high: int) -> None:
    low_i = max(0, int(low))
    high_i = max(low_i, int(high))
    handles = page.locator(".rheostat-handle[role='slider']")
    handles.first.wait_for(state="visible", timeout=15000)
    if handles.count() < 2:
        raise RuntimeError("Wellfound experience slider handles not found")

    # Left handle = min years, right handle = max years (0–10 scale on Wellfound).
    left = handles.nth(0)
    right = handles.nth(1)

    def _nudge(handle, target: int, valuemax: int = 10) -> None:
        handle.focus()
        page.wait_for_timeout(150)
        try:
            current = int(handle.get_attribute("aria-valuenow") or "0")
        except Exception:
            current = 0
        # Move with arrow keys (rheostat responds to keyboard).
        steps = target - current
        key = "ArrowRight" if steps > 0 else "ArrowLeft"
        for _ in range(abs(steps)):
            handle.press(key)
            page.wait_for_timeout(80)
        # Fallback: set attributes + fire change if keyboard did not land.
        try:
            now = int(handle.get_attribute("aria-valuenow") or "-1")
        except Exception:
            now = -1
        if now != target:
            handle.evaluate(
                """(el, vals) => {
                    const [value, max] = vals;
                    el.setAttribute("aria-valuenow", String(value));
                    el.setAttribute("aria-valuemax", String(max));
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                    const pct = max ? (100 * value) / max : 0;
                    el.style.left = pct + "%";
                }""",
                [target, valuemax],
            )
            page.wait_for_timeout(200)

    _nudge(left, low_i, 10)
    _nudge(right, high_i, 10)
    log.info("Filter: experience %s–%s years", low_i, high_i)


def _ensure_include_no_experience(page: Page, include: bool) -> None:
    box = page.locator("#includeJobsWithoutExperience")
    box.wait_for(state="attached", timeout=15000)
    checked = box.is_checked()
    if include and not checked:
        label = page.locator("label[for='includeJobsWithoutExperience']")
        if label.count():
            label.first.click(timeout=5000)
        else:
            box.check(force=True)
    elif not include and checked:
        label = page.locator("label[for='includeJobsWithoutExperience']")
        if label.count():
            label.first.click(timeout=5000)
        else:
            box.uncheck(force=True)
    page.wait_for_timeout(200)
    log.info("Filter: include jobs with no experience listed = %s", include)


def _click_view_results(page: Page) -> None:
    btn = page.locator("[data-test='SearchBar-ViewResultsButton']")
    if not btn.count():
        btn = page.get_by_role("button", name=re.compile(r"View results", re.I))
    btn.first.wait_for(state="visible", timeout=15000)
    btn.first.click(timeout=8000, force=True)
    page.wait_for_timeout(2500)
    log.info("Filter: View results clicked")


def apply_job_filters(page: Page, filters: dict) -> None:
    """Open Filters on /jobs and apply role / job-type / experience settings."""
    roles = [str(r).strip() for r in (filters.get("roles") or []) if str(r).strip()]
    job_types = [str(t).strip().lower() for t in (filters.get("job_types") or ["full_time"])]
    exp = filters.get("experience_years") or [0, 3]
    if not isinstance(exp, (list, tuple)) or len(exp) < 2:
        exp = [0, 3]
    include_none = bool(filters.get("include_jobs_without_experience", True))

    _click_filters_button(page)

    # Wait for the Job Types section (stable id from the live UI).
    try:
        page.locator("#form-input--jobTypes--full_time").wait_for(state="attached", timeout=15000)
    except PlaywrightTimeout as exc:
        raise RuntimeError("Filters popup did not open (job types missing).") from exc

    if roles:
        _ensure_roles(page, roles)

    if "full_time" in job_types:
        _ensure_full_time(page)

    _set_experience_years(page, int(exp[0]), int(exp[1]))
    _ensure_include_no_experience(page, include_none)
    _click_view_results(page)
