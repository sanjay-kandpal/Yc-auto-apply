from __future__ import annotations

import html
import logging
import os
import re
import sys

from dotenv import load_dotenv

from log_config import setup_logging
from mailer import send_html_email

load_dotenv()
log = logging.getLogger(__name__)

PURPOSES = {
    "password": "password reset",
    "name": "name reset",
}


def subject_for(purpose: str) -> str:
    label = PURPOSES.get(purpose, "login")
    return f"YC resume login — {label} code"


def html_body(code: str, purpose: str) -> str:
    label = PURPOSES.get(purpose, "login")
    return (
        "<p style='font-family:sans-serif'>Your YC resume login "
        f"{label} code:</p>"
        f"<p style='font-family:ui-monospace,Consolas,monospace;font-size:28px;"
        f"letter-spacing:6px'><strong>{html.escape(code)}</strong></p>"
        "<p style='font-family:sans-serif;color:#555'>This code expires in 10 minutes. "
        "Ignore this email if you did not request it.</p>"
    )


def validate_otp(code: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", code or ""))


def send_otp_email(code: str, purpose: str) -> None:
    if purpose not in PURPOSES:
        purpose = "password"
    if not validate_otp(code):
        raise SystemExit("RESUME_OTP must be a 6-digit code")
    send_html_email(subject_for(purpose), html_body(code, purpose))
    log.info("Sent resume OTP email (%s)", purpose)


def main() -> None:
    setup_logging()
    purpose = os.environ.get("RESUME_OTP_PURPOSE", "password").strip()
    code = os.environ.get("RESUME_OTP", "").strip()
    send_otp_email(code, purpose)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise
