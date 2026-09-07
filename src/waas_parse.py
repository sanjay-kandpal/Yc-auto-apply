from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import unquote

from config_loader import load_config

TITLE_KEYS = ("title", "role", "job_title")
ID_KEYS = ("id", "job_id", "objectID", "objectId")
DESC_KEYS = (
    "description",
    "desc",
    "jd",
    "job_description",
    "body",
    "about_the_job",
    "details",
    "html_description",
)


def job_url(waas_id: str | int) -> str:
    template = load_config()["search"].get(
        "job_url_template", "https://www.workatastartup.com/jobs/{job_id}"
    )
    return template.format(job_id=waas_id)


def parse_inertia_page(html_text: str) -> dict[str, Any] | None:
    match = re.search(r'data-page="([^"]+)"', html_text)
    if not match:
        match = re.search(r"data-page='([^']+)'", html_text)
    if not match:
        return None
    raw = html.unescape(unquote(match.group(1)))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"<[^>]+>", " ", value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_as_text(v) for v in value)
    if isinstance(value, dict):
        for key in DESC_KEYS:
            if key in value:
                return _as_text(value[key])
        return " ".join(_as_text(v) for v in value.values())
    return str(value)


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    lower = {k.lower(): v for k, v in d.items()}
    for key in keys:
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    return None


def _company_name(job: dict, parent: dict | None = None) -> str:
    company = job.get("company") or job.get("startup")
    if isinstance(company, dict):
        name = company.get("name") or company.get("company_name")
        if name:
            return str(name)
    for key in ("company_name", "company", "startup_name"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if parent:
        name = parent.get("name") or parent.get("company_name")
        if name:
            return str(name)
    return ""


def _waas_id(job: dict) -> str | None:
    path = job.get("show_path") or job.get("url") or job.get("path") or job.get("apply_url")
    if isinstance(path, str):
        match = re.search(r"/jobs/(\d+)", path)
        if match:
            return match.group(1)
    value = _first(job, ID_KEYS)
    if value is None:
        return None
    text = str(value)
    digits = re.search(r"(\d{3,})", text)
    return digits.group(1) if digits else text


def normalize_job(job: dict, parent: dict | None = None) -> dict[str, str] | None:
    role = _first(job, TITLE_KEYS)
    if not role or not isinstance(role, str):
        return None
    if parent and role.strip() == str(parent.get("name") or "").strip():
        if not any(k in job for k in ("description", "job_type", "show_path", "min_experience")):
            return None
    waas_id = _waas_id(job)
    company = _company_name(job, parent)
    if not company or not waas_id:
        return None
    jd = " ".join(
        _as_text(job.get(k))
        for k in (*DESC_KEYS, "location", "locations", "skills", "tags", "job_type", "visa")
        if job.get(k)
    ).strip()
    extra = []
    for key in ("location", "locations", "city", "remote", "min_experience", "salary", "equity"):
        if job.get(key) not in (None, "", False):
            extra.append(f"{key}: {_as_text(job[key])}")
    if extra:
        jd = (jd + "\n" + "\n".join(extra)).strip()
    url = job_url(waas_id)
    return {
        "waas_id": str(waas_id),
        "company": company.strip(),
        "role": role.strip(),
        "url": url,
        "jd_text": jd,
    }


def walk_jobs(payload: Any, parent: dict | None = None) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(item: dict, current_parent: dict | None) -> None:
        normalized = normalize_job(item, current_parent)
        if not normalized:
            return
        key = normalized["url"]
        if key in seen:
            return
        seen.add(key)
        found.append(normalized)

    def visit(node: Any, current_parent: dict | None) -> None:
        if isinstance(node, dict):
            jobs_list = node.get("jobs")
            if isinstance(jobs_list, list) and all(isinstance(j, dict) for j in jobs_list):
                for job in jobs_list:
                    add(job, node)
            if "title" in node or "role" in node or "job_title" in node:
                add(node, current_parent)
            next_parent = node if ("name" in node or "company_name" in node) else current_parent
            for value in node.values():
                visit(value, next_parent)
        elif isinstance(node, list):
            for item in node:
                visit(item, current_parent)

    visit(payload, parent)
    return found


def jobs_from_inertia_html(html_text: str) -> list[dict[str, str]]:
    page = parse_inertia_page(html_text)
    if not page:
        return []
    return walk_jobs(page.get("props", page))


def jobs_from_json_text(text: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    return walk_jobs(payload)
