from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()


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
    print(f"Sent email to {recipient}: {subject}")
