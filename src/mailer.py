from __future__ import annotations

import logging
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

_MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
_MIME_BY_SUFFIX = {
    ".png": ("image", "png"),
    ".jpg": ("image", "jpeg"),
    ".jpeg": ("image", "jpeg"),
    ".gif": ("image", "gif"),
    ".webm": ("video", "webm"),
    ".mp4": ("video", "mp4"),
}


def send_html_email(
    subject: str,
    html: str,
    to_addr: str | None = None,
    attachments: list[Path | str] | None = None,
) -> None:
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not address or not password:
        raise SystemExit("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
    recipient = (to_addr or address).strip()
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = recipient
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    for raw in attachments or []:
        path = Path(raw)
        if not path.is_file():
            log.warning("Skip missing email attachment: %s", path)
            continue
        size = path.stat().st_size
        if size > _MAX_ATTACHMENT_BYTES:
            log.warning("Skip oversized email attachment %s (%s bytes)", path.name, size)
            continue
        maintype, subtype = _MIME_BY_SUFFIX.get(path.suffix.lower(), ("application", "octet-stream"))
        part = MIMEBase(maintype, subtype)
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        msg.attach(part)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(address, password)
        smtp.sendmail(address, [recipient], msg.as_string())
    log.info("Sent email to %s: %s", recipient, subject)


def try_send_html_email(
    subject: str,
    html: str,
    to_addr: str | None = None,
    attachments: list[Path | str] | None = None,
) -> bool:
    try:
        send_html_email(subject, html, to_addr, attachments=attachments)
        return True
    except SystemExit as exc:
        log.warning("Could not send email: %s", exc)
        return False
    except Exception:
        log.exception("Could not send email")
        return False
