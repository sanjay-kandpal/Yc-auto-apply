from __future__ import annotations

import logging
import os
import time

import httpx
from dotenv import load_dotenv

from config_loader import load_config

load_dotenv()
log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_openrouter_last_call = 0.0


def complete(prompt: str) -> str:
    cfg = load_config()["draft"]
    provider = str(cfg.get("provider", "gemini")).lower()
    fallback = str(cfg.get("fallback_provider") or "").lower()
    try:
        return _dispatch(provider, prompt, cfg)
    except Exception as exc:
        if not fallback or fallback == provider:
            raise
        log.warning("%s failed (%s); falling back to %s", provider, exc, fallback)
        return _dispatch(fallback, prompt, cfg)


def _dispatch(provider: str, prompt: str, cfg: dict) -> str:
    if provider == "gemini":
        return _gemini(prompt, _env("LLM_API_KEY"), cfg.get("gemini_model", "gemini-2.5-flash"))
    if provider == "openrouter":
        return _openrouter(
            prompt,
            _env("OPENROUTER_API_KEY"),
            cfg.get("openrouter_model", "google/gemma-4-31b-it:free"),
            max(1, int(cfg.get("requests_per_minute", 5))),
        )
    raise RuntimeError(f"Unknown draft.provider: {provider}")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set.")
    return value


def _is_rate_limited(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        if exc.response.status_code == 429:
            return True
    text = str(exc)
    return "429" in text or "Too Many Requests" in text


def _pace(last_call: float, interval: float, label: str) -> float:
    if last_call <= 0:
        return time.time()
    wait = interval - (time.time() - last_call)
    if wait > 0:
        rpm = max(1, int(round(60.0 / interval)))
        log.debug("%s rate buffer: waiting %.1fs (max %s calls/min)", label, wait, rpm)
        time.sleep(wait)
    return time.time()


def _openrouter(prompt: str, api_key: str, model: str, rpm: int) -> str:
    global _openrouter_last_call
    interval = 60.0 / rpm
    _openrouter_last_call = _pace(_openrouter_last_call, interval, "OpenRouter")
    try:
        return _openrouter_post(prompt, api_key, model)
    except Exception as exc:
        if not _is_rate_limited(exc):
            raise
        log.warning("429 from OpenRouter — waiting 60s then retrying once")
        time.sleep(60)
        _openrouter_last_call = time.time()
        return _openrouter_post(prompt, api_key, model)


def _openrouter_post(prompt: str, api_key: str, model: str) -> str:
    gh = load_config().get("github") or {}
    owner = gh.get("owner") or "sanjay-kandpal"
    repo = gh.get("repo") or "Yc-auto-apply"
    resp = httpx.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": f"https://github.com/{owner}/{repo}",
            "X-Title": "YC auto-apply",
        },
        json={
            "model": model,
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    choices = resp.json().get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices")
    text = (choices[0].get("message") or {}).get("content") or ""
    text = text.strip()
    if not text:
        raise RuntimeError("OpenRouter returned empty text")
    return text


def _gemini(prompt: str, api_key: str, model: str) -> str:
    url = GEMINI_URL.format(model=model)
    resp = httpx.post(
        url,
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data.get('promptFeedback') or data.get('error') or 'empty'}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("Gemini returned empty text")
    return text
