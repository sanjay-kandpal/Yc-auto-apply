from __future__ import annotations

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config_loader import load_config, repo_path
from db import connect, jobs_with_status, update_job


def _load_resumes(cfg: dict) -> dict[str, str]:
    variants = {}
    for name, rel in cfg["match"]["resume_variants"].items():
        variants[name] = repo_path(rel).read_text(encoding="utf-8")
    if not variants:
        raise SystemExit("No resume variants configured.")
    return variants


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(k.lower() in text for k in keywords)


def hard_filter_reason(role: str, jd: str, cfg: dict) -> str | None:
    blob = f"{role}\n{jd}".lower()
    filters = cfg["filters"]
    if _contains_any(role.lower(), filters.get("skip_keywords", [])):
        return "skip_keyword"
    if filters.get("require_remote_or_india") and not _contains_any(
        blob, filters.get("remote_or_india_keywords", [])
    ):
        return "not_remote_or_india"
    if not _contains_any(blob, filters.get("role_keywords", [])):
        return "role_mismatch"
    return None


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}", text.lower()) if len(t) > 2}


def score_job(role: str, jd: str, resumes: dict[str, str]) -> tuple[float, str]:
    corpus = list(resumes.values()) + [f"{role}\n{jd}"]
    vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
    matrix = vectorizer.fit_transform(corpus)
    jd_vec = matrix[-1]
    resume_matrix = matrix[:-1]
    sims = cosine_similarity(resume_matrix, jd_vec).ravel()
    names = list(resumes.keys())
    best_i = int(sims.argmax())
    tfidf_score = float(sims[best_i]) * 100
    jd_tokens = _tokenize(f"{role}\n{jd}")
    resume_tokens = _tokenize(resumes[names[best_i]])
    overlap = (len(jd_tokens & resume_tokens) / max(len(resume_tokens), 1)) * 100
    combined = 0.7 * tfidf_score + 0.3 * overlap
    return round(min(100.0, combined), 2), names[best_i]


def match() -> None:
    cfg = load_config()
    resumes = _load_resumes(cfg)
    threshold = float(cfg["match"]["threshold"])
    conn = connect()
    jobs = jobs_with_status(conn, "discovered")
    scored = 0
    for job in jobs:
        if job["match_score"] is not None:
            continue
        reason = hard_filter_reason(job["role"] or "", job["jd_text"] or "", cfg)
        if reason:
            update_job(conn, job["id"], match_score=0.0, resume_variant="")
            print(f"filter {reason}: {job['company']} — {job['role']}")
            scored += 1
            continue
        score, variant = score_job(job["role"] or "", job["jd_text"] or "", resumes)
        update_job(conn, job["id"], match_score=score, resume_variant=variant)
        flag = "PASS" if score >= threshold else "low"
        print(f"{flag} {score:5.1f} [{variant}] {job['company']} — {job['role']}")
        scored += 1
    conn.commit()
    conn.close()
    print(f"Scored {scored} jobs.")


if __name__ == "__main__":
    match()
