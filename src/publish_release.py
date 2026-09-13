from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from config_loader import load_config
from log_config import setup_logging
from record_video import workflow_slug

load_dotenv()
log = logging.getLogger(__name__)

TAG_PREFIX = "recording-"


def release_tag(run_id: str | None = None, attempt: str | None = None) -> str:
    rid = (run_id or os.getenv("GITHUB_RUN_ID") or "local").strip() or "local"
    att = (attempt or os.getenv("GITHUB_RUN_ATTEMPT") or "1").strip() or "1"
    if att != "1":
        return f"{TAG_PREFIX}{rid}-{att}"
    return f"{TAG_PREFIX}{rid}"


def github_repo() -> str:
    env_repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if env_repo:
        return env_repo
    cfg = load_config().get("github") or {}
    return f"{cfg.get('owner')}/{cfg.get('repo')}".strip("/")


def release_page_url(tag: str, repo: str | None = None) -> str:
    return f"https://github.com/{repo or github_repo()}/releases/tag/{tag}"


def _meta(out_dir: Path) -> dict:
    path = out_dir / "meta.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_meta(out_dir: Path, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    github_out = os.getenv("GITHUB_OUTPUT", "").strip()
    if github_out:
        with open(github_out, "a", encoding="utf-8") as handle:
            handle.write(f"release_url={meta.get('release_url') or ''}\n")
            handle.write(f"tag={meta.get('tag') or ''}\n")


def publish_file(file_path: Path, tag: str, workflow: str) -> str | None:
    gh = shutil.which("gh")
    if not gh:
        log.info("gh CLI not found; skip GitHub Release.")
        return None
    if not os.getenv("GITHUB_TOKEN", "").strip() and not os.getenv("GH_TOKEN", "").strip():
        log.info("GITHUB_TOKEN not set; skip GitHub Release.")
        return None
    repo = github_repo()
    day = datetime.now(timezone.utc).date().isoformat()
    slug = workflow_slug(workflow)
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else ""
    notes = "\n".join(
        [
            f"Workflow: {slug}",
            f"Date (UTC): {day}",
            f"Actions run: {run_url}" if run_url else "",
            "Login is not recorded.",
            "The mp4 downloads; GitHub does not stream it inline in the browser.",
        ]
    ).strip()
    title = f"Spectate {slug} {day} ({tag})"
    delete = subprocess.run(
        [gh, "release", "delete", tag, "--repo", repo, "--yes", "--cleanup-tag"],
        capture_output=True,
        text=True,
    )
    if delete.returncode == 0:
        log.info("Replaced existing release %s", tag)
    cmd = [
        gh,
        "release",
        "create",
        tag,
        str(file_path),
        "--repo",
        repo,
        "--title",
        title,
        "--notes",
        notes,
        "--latest=false",
    ]
    log.info("Creating GitHub Release %s with %s", tag, file_path.name)
    subprocess.run(cmd, check=True)
    url = release_page_url(tag, repo)
    log.info("Published %s", url)
    return url


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Attach a spectate mp4 to a GitHub Release")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--workflow")
    parser.add_argument("--run-id")
    parser.add_argument("--attempt")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    meta = _meta(out_dir)
    file_path = Path(meta["path"]) if meta.get("path") else None
    tag = release_tag(args.run_id, args.attempt)
    meta["tag"] = tag
    if not file_path or not file_path.exists():
        log.info("No merged recording to publish.")
        meta["release_url"] = ""
        _write_meta(out_dir, meta)
        return
    try:
        meta["release_url"] = publish_file(file_path, tag, args.workflow or "") or ""
    except Exception:
        log.exception("GitHub Release publish failed")
        meta["release_url"] = ""
    _write_meta(out_dir, meta)


if __name__ == "__main__":
    main()
