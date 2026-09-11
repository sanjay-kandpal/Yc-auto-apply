from __future__ import annotations

import logging
import re
import time

from config_loader import load_config, repo_path
from db import connect, update_job
from llm import complete
from log_config import setup_logging

log = logging.getLogger(__name__)

PROMPT = """Write a {max_sentences}-sentence application note in first person for this YC-startup role.
Sound like a specific human engineer, not a cover-letter template. No greeting, no sign-off.
Ground every claim in the resume bullets. Mention the company or product only using facts from the JD.
End with exactly this line (do not omit it): GitHub: {github}

Resume bullets:
{resume}

Company: {company}
Role: {role}

Job description:
{jd}
"""

REPAIR_PROMPT = """Your previous draft failed these checks:
{failures}

Rewrite the note so it passes every check.
Rules: exactly {max_sentences} sentence(s) of body (not counting the GitHub line), first person, no greeting, no sign-off,
ground claims in the resume, company/product facts only from the JD.
End with exactly this line: GitHub: {github}

Company: {company}
Role: {role}

Resume bullets:
{resume}

Job description:
{jd}

Previous draft:
{draft}
"""

_GREETING = re.compile(
    r"^\s*(hi\b|hello\b|hey\b|dear\b|good\s+(morning|afternoon|evening)\b)",
    re.I,
)
_SIGNOFF = re.compile(
    r"(thanks(?:\s+you)?|thank\s+you|best\s+regards|regards|sincerely|cheers|warmly)\s*[,!]?\s*$",
    re.I,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|i'll|me|my|mine)\b", re.I)


def github_profile_url(cfg: dict | None = None) -> str:
    data = cfg or load_config()
    gh = data.get("github") or {}
    url = str(gh.get("profile_url") or "").strip()
    if url:
        return url.rstrip("/")
    owner = str(gh.get("owner") or "").strip()
    return f"https://github.com/{owner}" if owner else ""


def with_github(text: str, cfg: dict | None = None) -> str:
    url = github_profile_url(cfg)
    body = (text or "").strip()
    if not url:
        return body
    handle = url.rstrip("/").split("/")[-1].lower()
    lowered = body.lower()
    if url.lower() in lowered or f"github.com/{handle}" in lowered:
        return body
    return f"{body}\n\nGitHub: {url}" if body else f"GitHub: {url}"


def _split_body_and_github(text: str, github: str) -> tuple[str, str | None]:
    lines = [ln.rstrip() for ln in (text or "").strip().splitlines()]
    github_line = None
    body_lines = []
    handle = github.rstrip("/").split("/")[-1].lower() if github else ""
    for ln in lines:
        lowered = ln.lower().strip()
        if github and (
            lowered == f"github: {github.lower()}"
            or (lowered.startswith("github:") and handle and handle in lowered)
        ):
            github_line = ln.strip()
            continue
        body_lines.append(ln)
    body = "\n".join(body_lines).strip()
    return body, github_line


def _sentence_count(body: str) -> int:
    text = re.sub(r"\s+", " ", (body or "").strip())
    if not text:
        return 0
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    if len(parts) <= 1 and text and text[-1] not in ".!?":
        return 1 if text else 0
    return len(parts)


def validate_draft(text: str, github: str, max_sentences: int = 2, max_chars: int = 900) -> list[str]:
    failures: list[str] = []
    raw = (text or "").strip()
    if not raw:
        return ["draft is empty"]

    body, github_line = _split_body_and_github(raw, github)
    if github:
        expected = f"GitHub: {github}"
        if not github_line:
            failures.append(f"missing exact closing line: {expected}")
        elif github_line != expected and github.lower() not in github_line.lower():
            failures.append(f"GitHub line must be: {expected}")

    if not body:
        failures.append("missing application body before GitHub line")
        return failures

    if _GREETING.search(body):
        failures.append("starts with a greeting (Hi/Hello/Dear/...)")
    if _SIGNOFF.search(body):
        failures.append("ends with a sign-off (Thanks/Regards/...)")
    if not _FIRST_PERSON.search(body):
        failures.append("not clearly first person (need I/I'm/I've/my)")

    n = _sentence_count(body)
    if n != max_sentences:
        failures.append(f"body has {n} sentence(s); need exactly {max_sentences}")

    if len(body) > max_chars:
        failures.append(f"body too long ({len(body)} chars; max {max_chars})")

    return failures


def _resume_text(cfg: dict, variant: str) -> str:
    rel = cfg["match"]["resume_variants"][variant]
    return repo_path(rel).read_text(encoding="utf-8")


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "Too Many Requests" in text


def _pace(last_call: float, interval: float) -> float:
    if last_call <= 0:
        return time.time()
    wait = interval - (time.time() - last_call)
    if wait > 0:
        log.debug("Rate buffer: waiting %.1fs (max 5 calls/min)", wait)
        time.sleep(wait)
    return time.time()


def _complete_paced(prompt: str, last_call: float, interval: float) -> tuple[str, float]:
    last_call = _pace(last_call, interval)
    try:
        return complete(prompt), last_call
    except Exception as exc:
        if not _is_rate_limited(exc):
            raise
        log.warning("429 from model — waiting 60s then retrying once")
        time.sleep(60)
        last_call = time.time()
        return complete(prompt), last_call


def _draft_with_validation(
    *,
    cfg: dict,
    resume: str,
    company: str,
    role: str,
    jd: str,
    github: str,
    last_call: float,
    interval: float,
) -> tuple[str, float, list[str]]:
    max_sentences = max(1, int(cfg["draft"].get("max_sentences", 2)))
    max_chars = max(200, int(cfg["draft"].get("max_chars", 900)))
    max_retries = max(0, int(cfg["draft"].get("validation_retries", 2)))

    prompt = PROMPT.format(
        resume=resume,
        company=company,
        role=role,
        jd=jd,
        github=github,
        max_sentences=max_sentences,
    )
    answer, last_call = _complete_paced(prompt, last_call, interval)
    answer = with_github(answer, cfg)
    failures = validate_draft(answer, github, max_sentences=max_sentences, max_chars=max_chars)

    attempt = 0
    while failures and attempt < max_retries:
        attempt += 1
        log.warning(
            "Draft retry %s/%s for %s — %s: %s",
            attempt,
            max_retries,
            company,
            role,
            "; ".join(failures),
        )
        repair = REPAIR_PROMPT.format(
            failures="\n".join(f"- {f}" for f in failures),
            max_sentences=max_sentences,
            github=github,
            company=company,
            role=role,
            resume=resume,
            jd=jd,
            draft=answer,
        )
        answer, last_call = _complete_paced(repair, last_call, interval)
        answer = with_github(answer, cfg)
        failures = validate_draft(answer, github, max_sentences=max_sentences, max_chars=max_chars)

    return answer, last_call, failures


def draft() -> None:
    setup_logging()
    cfg = load_config()
    threshold = float(cfg["match"]["threshold"])
    rpm = max(1, int(cfg["draft"].get("requests_per_minute", 5)))
    interval = 60.0 / rpm
    conn = connect()
    rows = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'discovered'
          AND match_score IS NOT NULL
          AND match_score >= ?
          AND (draft_answer IS NULL OR draft_answer = '')
          AND resume_variant IS NOT NULL
          AND resume_variant != ''
        ORDER BY match_score DESC
        """,
        (threshold,),
    ).fetchall()
    log.info("%s jobs above threshold to draft (%s/min).", len(rows), rpm)
    last_call = 0.0
    github = github_profile_url(cfg)
    for job in rows:
        resume = _resume_text(cfg, job["resume_variant"])
        jd = (job["jd_text"] or "")[:6000]
        try:
            answer, last_call, failures = _draft_with_validation(
                cfg=cfg,
                resume=resume,
                company=job["company"],
                role=job["role"],
                jd=jd,
                github=github,
                last_call=last_call,
                interval=interval,
            )
        except Exception:
            log.exception("LLM failed for %s", job["id"])
            continue
        if failures:
            log.error(
                "Draft still invalid for %s — %s: %s; saving best effort",
                job["company"],
                job["role"],
                failures,
            )
        update_job(conn, job["id"], draft_answer=answer, status="drafted")
        conn.commit()
        log.info("drafted %s — %s", job["company"], job["role"])
        log.debug("draft text for %s: %s", job["id"], answer)
    conn.close()


if __name__ == "__main__":
    draft()
