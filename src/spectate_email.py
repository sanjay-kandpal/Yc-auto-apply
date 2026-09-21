from __future__ import annotations

import argparse
import html
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from config_loader import load_config
from log_config import setup_logging
from mailer import try_send_html_email

load_dotenv()
log = logging.getLogger(__name__)


def board_label(workflow: str | None = None) -> str:
    """YC vs Wellfound from workflow name / Actions env."""
    text = (
        workflow
        or os.getenv("SPECTATE_WORKFLOW")
        or os.getenv("GITHUB_WORKFLOW")
        or ""
    ).lower()
    if "wellfound" in text:
        return "Wellfound"
    return "YC"


def actions_run_url() -> str:
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if not run_id:
        return ""
    cfg = load_config().get("github") or {}
    owner = cfg.get("owner") or ""
    repo = cfg.get("repo") or ""
    if not owner or not repo:
        return ""
    return f"https://github.com/{owner}/{repo}/actions/runs/{run_id}"


def recording_release_url(out_dir: Path | None = None) -> str:
    env_url = os.getenv("RECORDING_RELEASE_URL", "").strip()
    if env_url:
        return env_url
    if not out_dir:
        return ""
    path = out_dir / "meta.json"
    if not path.exists():
        return ""
    meta = json.loads(path.read_text(encoding="utf-8"))
    return str(meta.get("release_url") or "").strip()


def send_scan_recap(out_dir: Path | None = None, workflow: str | None = None) -> None:
    setup_logging()
    video = recording_release_url(out_dir)
    run_url = actions_run_url()
    if not video and not run_url:
        log.info("Skip scan recording email: no release and no Actions run URL.")
        return
    day = datetime.now(timezone.utc).date().isoformat()
    board = board_label(workflow)
    parts = [
        f"<p style='font-family:sans-serif'>{html.escape(board)} scan browser session recording.</p>",
        "<p style='font-family:sans-serif;color:#555'>Login is not recorded. "
        "The GitHub Release link downloads the mp4 (it does not play inline).</p>",
    ]
    if video:
        parts.append(
            f'<p style="font-family:sans-serif"><a href="{html.escape(video)}">'
            f"Download this {html.escape(board)} scan run</a></p>"
        )
    if run_url:
        parts.append(
            f'<p style="font-family:sans-serif"><a href="{html.escape(run_url)}">'
            "GitHub Actions run (mp4 artifact)</a></p>"
        )
    if try_send_html_email(f"{board} scan recording — {day}", "".join(parts)):
        log.info("Sent %s scan recording email.", board)
    else:
        log.warning("Could not send %s scan recording email.", board)


def main() -> None:
    parser = argparse.ArgumentParser(description="Email a scan-run recording link")
    parser.add_argument("--out-dir", default="")
    parser.add_argument(
        "--workflow",
        default="",
        help="Workflow name (e.g. scan-wellfound). Defaults to SPECTATE_WORKFLOW / GITHUB_WORKFLOW.",
    )
    args = parser.parse_args()
    send_scan_recap(
        Path(args.out_dir) if args.out_dir else None,
        workflow=args.workflow or None,
    )


if __name__ == "__main__":
    main()
