from __future__ import annotations

import hashlib
import hmac
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from urllib.parse import urlencode


def _b64url(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return urlsafe_b64decode(text + padding)


def sign(job_id: str, action: str, expiry: int, secret: str) -> str:
    msg = f"{job_id}|{action}|{expiry}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return _b64url(digest)


def verify(job_id: str, action: str, expiry: int | str, token: str, secret: str) -> bool:
    try:
        expiry_i = int(expiry)
    except (TypeError, ValueError):
        return False
    if expiry_i < int(time.time()):
        return False
    if action not in ("approve", "reject"):
        return False
    expected = sign(job_id, action, expiry_i, secret)
    try:
        return hmac.compare_digest(_b64url_decode(expected), _b64url_decode(token))
    except Exception:
        return False


def approval_link(base_url: str, job_id: str, action: str, expiry: int, secret: str) -> str:
    token = sign(job_id, action, expiry, secret)
    query = urlencode(
        {"job_id": job_id, "action": action, "expiry": str(expiry), "token": token}
    )
    return f"{base_url.rstrip('/')}/?{query}"


def token_expiry(ttl_hours: int) -> int:
    return int(time.time()) + ttl_hours * 3600


if __name__ == "__main__":
    secret = "test-secret"
    expiry = int(time.time()) + 3600
    token = sign("abc", "approve", expiry, secret)
    assert verify("abc", "approve", expiry, token, secret)
    assert not verify("abc", "reject", expiry, token, secret)
    assert not verify("abc", "approve", 1, token, secret)
    print("token self-check ok")
