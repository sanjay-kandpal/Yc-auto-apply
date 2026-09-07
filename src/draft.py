from __future__ import annotations

import time

from config_loader import load_config, repo_path
from db import connect, update_job
from llm import complete


PROMPT = """Write a 3–5 sentence application note in first person for this YC-startup role.
Sound like a specific human engineer, not a cover-letter template. No greeting, no sign-off.
Ground every claim in the resume bullets. Mention the company or product only using facts from the JD.

Resume bullets:
{resume}

Company: {company}
Role: {role}

Job description:
{jd}
"""


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
        print(f"Rate buffer: waiting {wait:.1f}s (max 5 calls/min)")
        time.sleep(wait)
    return time.time()


def _complete_paced(prompt: str, last_call: float, interval: float) -> tuple[str, float]:
    last_call = _pace(last_call, interval)
    try:
        return complete(prompt), last_call
    except Exception as exc:
        if not _is_rate_limited(exc):
            raise
        print("429 from model — waiting 60s then retrying once")
        time.sleep(60)
        last_call = time.time()
        return complete(prompt), last_call


def draft() -> None:
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
    print(f"{len(rows)} jobs above threshold to draft ({rpm}/min).")
    last_call = 0.0
    for job in rows:
        prompt = PROMPT.format(
            resume=_resume_text(cfg, job["resume_variant"]),
            company=job["company"],
            role=job["role"],
            jd=(job["jd_text"] or "")[:6000],
        )
        try:
            answer, last_call = _complete_paced(prompt, last_call, interval)
        except Exception as exc:
            print(f"LLM failed for {job['id']}: {exc}")
            continue
        update_job(conn, job["id"], draft_answer=answer, status="drafted")
        conn.commit()
        print(f"drafted {job['company']} — {job['role']}\n{answer}\n")
    conn.close()


if __name__ == "__main__":
    draft()
