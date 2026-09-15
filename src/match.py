from __future__ import annotations

import json
import logging
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config_loader import load_config, repo_path
from db import connect, ensure_resume_version, jobs_with_status, update_job
from log_config import setup_logging

log = logging.getLogger(__name__)

WEIGHTS_VERSION = "v1"
COSINE_WEIGHT = 0.7
OVERLAP_WEIGHT = 0.3


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


def evaluate_hard_filters(role: str, jd: str, cfg: dict) -> tuple[dict, str | None]:
    blob = f"{role}\n{jd}".lower()
    filters = cfg["filters"]
    skip_ok = not _contains_any(role.lower(), filters.get("skip_keywords", []))
    if filters.get("require_remote_or_india"):
        remote_ok = _contains_any(blob, filters.get("remote_or_india_keywords", []))
    else:
        remote_ok = True
    role_ok = _contains_any(blob, filters.get("role_keywords", []))
    hard = {
        "skip_keyword": skip_ok,
        "remote_or_india": remote_ok,
        "role_keyword": role_ok,
        "passed": skip_ok and remote_ok and role_ok,
    }
    return hard, hard_filter_reason(role, jd, cfg)


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}", text.lower()) if len(t) > 2}


def _combined(cosine: float, overlap: float) -> float:
    return round(min(100.0, COSINE_WEIGHT * cosine + OVERLAP_WEIGHT * overlap), 2)


def empty_breakdown(hard: dict, reason: str | None) -> dict:
    return {
        "weights_version": WEIGHTS_VERSION,
        "hard_filters": hard,
        "filter_reason": reason,
        "variant_scores": {},
        "winning_variant": "",
        "cosine_score": 0.0,
        "keyword_overlap_score": 0.0,
        "final_score": 0.0,
        "top_keywords": [],
    }


def score_job(role: str, jd: str, resumes: dict[str, str], hard: dict | None = None) -> dict:
    names = list(resumes.keys())
    corpus = list(resumes.values()) + [f"{role}\n{jd}"]
    vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
    matrix = vectorizer.fit_transform(corpus)
    jd_vec = matrix[-1]
    resume_matrix = matrix[:-1]
    sims = cosine_similarity(resume_matrix, jd_vec).ravel()
    jd_tokens = _tokenize(f"{role}\n{jd}")
    variant_scores = {}
    for i, name in enumerate(names):
        cosine = float(sims[i]) * 100
        resume_tokens = _tokenize(resumes[name])
        overlap = (len(jd_tokens & resume_tokens) / max(len(resume_tokens), 1)) * 100
        variant_scores[name] = {
            "cosine": round(cosine, 2),
            "overlap": round(overlap, 2),
            "combined": _combined(cosine, overlap),
        }
    best_i = int(sims.argmax())
    winner = names[best_i]
    cosine_score = variant_scores[winner]["cosine"]
    overlap_score = variant_scores[winner]["overlap"]
    features = vectorizer.get_feature_names_out()
    jd_weights = jd_vec.toarray().ravel()
    resume_weights = resume_matrix[best_i].toarray().ravel()
    ranked = []
    for idx, term in enumerate(features):
        if jd_weights[idx] > 0 and resume_weights[idx] > 0:
            ranked.append({"term": str(term), "weight": round(float(jd_weights[idx]), 4)})
    ranked.sort(key=lambda item: item["weight"], reverse=True)
    return {
        "weights_version": WEIGHTS_VERSION,
        "hard_filters": hard or {"passed": True},
        "filter_reason": None,
        "variant_scores": variant_scores,
        "winning_variant": winner,
        "cosine_score": cosine_score,
        "keyword_overlap_score": overlap_score,
        "final_score": _combined(cosine_score, overlap_score),
        "top_keywords": ranked[:5],
    }


def match() -> None:
    setup_logging()
    cfg = load_config()
    resumes = _load_resumes(cfg)
    threshold = float(cfg["match"]["threshold"])
    conn = connect()
    jobs = jobs_with_status(conn, "discovered")
    scored = 0
    for job in jobs:
        if job["match_score"] is not None:
            continue
        role = job["role"] or ""
        jd = job["jd_text"] or ""
        hard, reason = evaluate_hard_filters(role, jd, cfg)
        if reason:
            breakdown = empty_breakdown(hard, reason)
            update_job(
                conn,
                job["id"],
                match_score=0.0,
                resume_variant="",
                match_breakdown=json.dumps(breakdown),
                resume_version_hash=None,
            )
            log.debug("filter %s: %s — %s", reason, job["company"], job["role"])
            scored += 1
            continue
        breakdown = score_job(role, jd, resumes, hard=hard)
        variant = breakdown["winning_variant"]
        digest = ensure_resume_version(conn, variant, resumes[variant])
        score = breakdown["final_score"]
        update_job(
            conn,
            job["id"],
            match_score=score,
            resume_variant=variant,
            match_breakdown=json.dumps(breakdown),
            resume_version_hash=digest,
        )
        flag = "PASS" if score >= threshold else "low"
        log.debug("%s %s [%s] %s — %s", flag, f"{score:5.1f}", variant, job["company"], job["role"])
        scored += 1
    conn.commit()
    conn.close()
    log.info("Scored %s jobs.", scored)


if __name__ == "__main__":
    match()
