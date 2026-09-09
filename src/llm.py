from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

from config_loader import load_config

load_dotenv()

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def complete(prompt: str) -> str:
    cfg = load_config()["draft"]
    provider = str(cfg.get("provider", "gemini")).lower()
    fallback = str(cfg.get("fallback_provider") or "").lower()
    try:
        return _dispatch(provider, prompt, cfg)
    except Exception as exc:
        if not fallback or fallback == provider:
            raise
        print(f"{provider} failed ({exc}); falling back to {fallback}")
        return _dispatch(fallback, prompt, cfg)


def _dispatch(provider: str, prompt: str, cfg: dict) -> str:
    if provider == "gemini":
        return _gemini(prompt, _env("LLM_API_KEY"), cfg.get("gemini_model", "gemini-2.5-flash"))
    if provider == "openrouter":
        return _openrouter(
            prompt,
            _env("OPENROUTER_API_KEY"),
            cfg.get("openrouter_model", "google/gemma-4-31b-it:free"),
        )
    raise RuntimeError(f"Unknown draft.provider: {provider}")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set.")
    return value


def _openrouter(prompt: str, api_key: str, model: str) -> str:
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
