from __future__ import annotations

import argparse
import html
import re
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from config_loader import load_config
from db import connect
from mailer import send_html_email

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
REPORT_HOUR = 22  # 10pm IST
_WS = re.compile(r"\s+")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


def _close_at(day: datetime) -> datetime:
    return day.astimezone(IST).replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)


def _report_window(
    now: datetime | None = None,
    close_date: str | None = None,
) -> tuple[datetime, datetime, str]:
    """Most recent completed 10pm→10pm IST period.

    If scan holds the jobs-db lock past midnight, a delayed run still uses last
    night's 10pm close — not the new calendar day — so that day's applies are kept.
    Windows are [start, end) so adjacent reports do not double-count.
    """
    if close_date:
        day = datetime.fromisoformat(close_date.strip()).date()
        end = datetime(day.year, day.month, day.day, REPORT_HOUR, 0, 0, tzinfo=IST)
    else:
        now_ist = (now or datetime.now(tz=IST)).astimezone(IST)
        today_close = _close_at(now_ist)
        end = today_close if now_ist >= today_close else today_close - timedelta(days=1)
    start = end - timedelta(days=1)
    return start, end, end.date().isoformat()


def _in_window(ts: str | None, start: datetime, end: datetime) -> bool:
    dt = _parse_ts(ts)
    if dt is None:
        return False
    return start <= dt < end


def _normalize_error(message: str | None) -> str:
    text = _WS.sub(" ", (message or "").strip())
    return text or "No error recorded"


def _job_line(job) -> str:
    company = html.escape(job["company"] or "")
    role = html.escape(job["role"] or "")
    url = html.escape(job["url"] or "")
    return f'<li><strong>{company}</strong> — {role} · <a href="{url}">listing</a></li>'


def build_report_html(submitted: list, failed: list, date_ist: str) -> str:
    groups: dict[str, list] = defaultdict(list)
    for job in failed:
        groups[_normalize_error(job["error_message"])].append(job)

    submitted_block = (
        "<ul>" + "".join(_job_line(j) for j in submitted) + "</ul>"
        if submitted
        else "<p>None.</p>"
    )
    failed_items = []
    for job in failed:
        company = html.escape(job["company"] or "")
        role = html.escape(job["role"] or "")
        url = html.escape(job["url"] or "")
        err = html.escape(_normalize_error(job["error_message"]))
        failed_items.append(
            f'<li><strong>{company}</strong> — {role} · <a href="{url}">listing</a>'
            f'<br/><span style="color:#666">Error: {err}</span></li>'
        )
    failed_block = "<ul>" + "".join(failed_items) + "</ul>" if failed else "<p>None.</p>"

    group_parts = []
    for err, jobs in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        group_parts.append(
            f"""
            <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:0 0 12px">
              <p style="margin:0 0 8px"><strong>{len(jobs)}×</strong> — {html.escape(err)}</p>
              <ul style="margin:0">{"".join(_job_line(j) for j in jobs)}</ul>
            </div>
            """
        )
    groups_block = "".join(group_parts) if group_parts else "<p>None.</p>"

    return f"""
    <div style="font-family:sans-serif;max-width:640px">
      <h1 style="margin:0 0 8px">YC daily report</h1>
      <p style="margin:0 0 16px;color:#555">10pm IST window ending {html.escape(date_ist)} (previous 10pm → this 10pm)</p>
      <p style="margin:0 0 16px">
        <strong>{len(submitted)}</strong> successfully applied ·
        <strong>{len(failed)}</strong> failed
      </p>
      <h2 style="margin:24px 0 8px">Successfully applied</h2>
      {submitted_block}
      <h2 style="margin:24px 0 8px">Failed</h2>
      {failed_block}
      <h2 style="margin:24px 0 8px">Errors grouped</h2>
      {groups_block}
    </div>
    """


def send_daily_report(now: datetime | None = None, close_date: str | None = None) -> None:
    cfg = load_config()
    start, end, date_ist = _report_window(now=now, close_date=close_date)
    conn = connect()
    rows = list(conn.execute("SELECT * FROM jobs"))
    conn.close()

    submitted = [
        row
        for row in rows
        if row["status"] == "submitted" and _in_window(row["submitted_at"], start, end)
    ]
    failed = [
        row
        for row in rows
        if row["status"] == "failed" and _in_window(row["decided_at"], start, end)
    ]
    submitted.sort(key=lambda r: r["submitted_at"] or "", reverse=True)
    failed.sort(key=lambda r: r["decided_at"] or "", reverse=True)

    template = cfg.get("email", {}).get(
        "daily_report_subject",
        "YC daily report — {submitted} applied, {failed} failed ({date})",
    )
    subject = template.format(submitted=len(submitted), failed=len(failed), date=date_ist)
    body = build_report_html(submitted, failed, date_ist)
    send_html_email(subject, body)
    print(
        f"Daily report: {len(submitted)} submitted, {len(failed)} failed "
        f"for {start.isoformat()} → {end.isoformat()} (close {date_ist})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Email the 10pm IST daily apply report")
    parser.add_argument(
        "--date",
        dest="close_date",
        help="IST date whose 10pm closes the window (YYYY-MM-DD). Default: last completed 10pm period.",
    )
    args = parser.parse_args()
    send_daily_report(close_date=args.close_date)


if __name__ == "__main__":
    main()
