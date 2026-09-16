from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse

from config_loader import load_config

TITLE_KEYS = ("title", "role", "job_title", "name")
ID_KEYS = ("id", "job_id", "legacyId", "legacy_id", "slug")
DESC_KEYS = (
    "description",
    "desc",
    "jd",
    "job_description",
    "body",
    "about_the_job",
    "details",
    "html_description",
    "descriptionHtml",
    "description_html",
)
OWN_HOSTS = ("wellfound.com", "angel.co", "angel.co.uk")
EXTERNAL_HINTS = (
    "apply_on_company",
    "company_website",
    "external_ats",
    "greenhouse",
    "lever.co",
    "ashbyhq",
)


def job_url(job_id: str | int, slug: str = "") -> str:
    template = (load_config().get("wellfound") or {}).get("search", {}).get(
        "job_url_template", "https://wellfound.com/jobs/{job_id}"
    )
    ident = str(job_id).strip()
    if slug:
        ident = f"{ident}-{slug}".strip("-")
    return template.format(job_id=ident)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"<[^>]+>", " ", value)
    if isinstance(value, bool):
        return "true" if value else "false"
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
    company = job.get("company") or job.get("startup") or job.get("organization")
    if isinstance(company, dict):
        name = company.get("name") or company.get("company_name") or company.get("displayName")
        if name:
            return str(name)
    for key in ("company_name", "companyName", "startup_name", "startupName"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if parent:
        name = parent.get("name") or parent.get("company_name") or parent.get("displayName")
        if name:
            return str(name)
    return ""


def _job_id(job: dict) -> str | None:
    path = job.get("show_path") or job.get("url") or job.get("path") or job.get("slug")
    if isinstance(path, str):
        match = re.search(r"/jobs/([^/?#]+)", path)
        if match:
            return match.group(1)
    value = _first(job, ID_KEYS)
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("Job:"):
        text = text.split(":", 1)[-1]
    return text or None


def _offsite_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return not any(host == own or host.endswith("." + own) for own in OWN_HOSTS)


def is_external_apply(job: dict) -> bool:
    blob = json.dumps(job, default=str).lower()
    if any(hint in blob for hint in ("apply on company", "apply via company")):
        return True
    for key in ("applyUrl", "apply_url", "applicationUrl", "companyApplyUrl"):
        value = job.get(key)
        if isinstance(value, str) and value.startswith("http") and _offsite_host(value):
            return True
    ats = job.get("ats") or job.get("jobType") or job.get("job_type")
    if isinstance(ats, str) and any(hint in ats.lower() for hint in EXTERNAL_HINTS):
        return True
    return False


def normalize_job(job: dict, parent: dict | None = None) -> dict[str, str] | None:
    role = _first(job, TITLE_KEYS)
    if not role or not isinstance(role, str):
        return None
    if parent and role.strip() == str(parent.get("name") or "").strip():
        if not any(k in job for k in ("description", "slug", "startup", "company")):
            return None
    ident = _job_id(job)
    company = _company_name(job, parent)
    if not company or not ident:
        return None
    if is_external_apply(job):
        return None
    slug = ""
    raw_slug = job.get("slug")
    if isinstance(raw_slug, str) and raw_slug.strip() and raw_slug.strip() not in ident:
        slug = raw_slug.strip()
    jd = " ".join(
        _as_text(job.get(k))
        for k in (*DESC_KEYS, "location", "locations", "locationNames", "skills", "tags")
        if job.get(k)
    ).strip()
    extra = []
    for key in ("location", "locations", "locationNames", "city", "remote", "remoteFriendly", "salary"):
        if job.get(key) not in (None, "", False):
            extra.append(f"{key}: {_as_text(job[key])}")
    if extra:
        jd = (jd + "\n" + "\n".join(extra)).strip()
    url = job.get("url") if isinstance(job.get("url"), str) and job.get("url", "").startswith("http") else ""
    if url and _offsite_host(url):
        url = job_url(ident, slug)
    if not url:
        url = job_url(ident, slug)
    return {
        "wellfound_id": str(ident),
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

    def looks_like_job(node: dict) -> bool:
        has_title = any(k in node for k in ("title", "role", "job_title"))
        has_company = any(k in node for k in ("startup", "company", "company_name", "startup_name"))
        return has_title and (has_company or "description" in node or "slug" in node)

    def visit(node: Any, current_parent: dict | None) -> None:
        if isinstance(node, dict):
            jobs_list = node.get("jobs") or node.get("jobSearchResults") or node.get("results")
            if isinstance(jobs_list, list) and all(isinstance(j, dict) for j in jobs_list):
                for job in jobs_list:
                    inner = job.get("job") or job.get("node") or job
                    if isinstance(inner, dict):
                        add(inner, node if "name" in node else current_parent)
            if looks_like_job(node):
                add(node, current_parent)
            next_parent = node if ("name" in node or "company_name" in node) else current_parent
            for value in node.values():
                visit(value, next_parent)
        elif isinstance(node, list):
            for item in node:
                visit(item, current_parent)

    visit(payload, parent)
    return found


def jobs_from_html(html_text: str) -> list[dict[str, str]]:
    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html_text,
        re.I | re.S,
    )
    if not match:
        return []
    raw = html.unescape(match.group(1))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return walk_jobs(payload)


def jobs_from_json_text(text: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    return walk_jobs(payload)
