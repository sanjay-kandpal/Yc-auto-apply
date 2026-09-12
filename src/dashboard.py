from __future__ import annotations

import html
import logging

from config_loader import ROOT
from db import all_jobs, connect
from export_jobs import export as export_jobs
from log_config import setup_logging

log = logging.getLogger(__name__)
OUT = ROOT / "docs" / "index.html"


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def render() -> None:
    setup_logging()
    conn = connect()
    jobs = all_jobs(conn)
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job["status"] or "unknown"] = counts.get(job["status"] or "unknown", 0) + 1
    summary = " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no jobs yet"
    rows = []
    for job in jobs:
        rows.append(
            "<tr>"
            f"<td>{_esc(job['status'])}</td>"
            f"<td>{_esc(job['match_score'])}</td>"
            f"<td>{_esc(job['company'])}</td>"
            f"<td>{_esc(job['role'])}</td>"
            f"<td>{_esc(job['resume_variant'])}</td>"
            f"<td><a href='{_esc(job['url'])}'>link</a></td>"
            f"<td>{_esc(job['discovered_at'])}</td>"
            "</tr>"
        )
    table = "\n".join(rows) or "<tr><td colspan='7'>Empty database.</td></tr>"
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>YC job bot status</title>
  <style>
    body {{ font-family: sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: 8px; text-align: left; font-size: 14px; }}
    th {{ background: #f4f4f4; }}
  </style>
</head>
<body>
  <h1>YC job bot</h1>
  <p>{_esc(summary)}</p>
  <table>
    <thead>
      <tr>
        <th>status</th><th>score</th><th>company</th><th>role</th>
        <th>resume</th><th>url</th><th>discovered</th>
      </tr>
    </thead>
    <tbody>
      {table}
    </tbody>
  </table>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    log.info("Wrote %s (%s jobs)", OUT, len(jobs))
    export_jobs(conn)
    conn.close()


if __name__ == "__main__":
    render()
