from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

from config_loader import load_config

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def complete(prompt: str) -> str:
    cfg = load_config()["draft"]
    provider = str(cfg.get("provider", "groq")).lower()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("LLM_API_KEY is not set.")
    if provider == "groq":
        return _groq(prompt, api_key, cfg.get("groq_model", "llama-3.3-70b-versatile"))
    if provider == "gemini":
        return _gemini(prompt, api_key, cfg.get("gemini_model", "gemini-2.0-flash"))
    raise SystemExit(f"Unknown draft.provider: {provider}")


def _groq(prompt: str, api_key: str, model: str) -> str:
    resp = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _gemini(prompt: str, api_key: str, model: str) -> str:
    url = GEMINI_URL.format(model=model)
    resp = httpx.post(
        url,
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    resp.raise_for_status()
    parts = resp.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()
