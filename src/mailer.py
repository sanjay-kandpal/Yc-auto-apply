from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


def send_html_email(subject: str, html: str, to_addr: str | None = None) -> None:
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not address or not password:
        raise SystemExit("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
    recipient = (to_addr or address).strip()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(address, password)
        smtp.sendmail(address, [recipient], msg.as_string())
    log.info("Sent email to %s: %s", recipient, subject)


def try_send_html_email(subject: str, html: str, to_addr: str | None = None) -> bool:
    try:
        send_html_email(subject, html, to_addr)
        return True
    except SystemExit as exc:
        log.warning("Could not send email: %s", exc)
        return False
    except Exception:
        log.exception("Could not send email")
        return False
