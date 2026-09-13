from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess

from dotenv import load_dotenv

from config_loader import load_config
from log_config import setup_logging
from publish_release import TAG_PREFIX, github_repo

load_dotenv()
log = logging.getLogger(__name__)


def keep_count() -> int:
    return int((load_config().get("spectate") or {}).get("keep_releases") or 30)


def tags_to_delete(entries: list[dict], keep: int) -> list[str]:
    tagged = [item for item in entries if str(item.get("tagName") or "").startswith(TAG_PREFIX)]
    tagged.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    return [str(item["tagName"]) for item in tagged[max(0, keep) :]]


def list_releases(repo: str) -> list[dict]:
    gh = shutil.which("gh")
    if not gh:
        log.warning("gh CLI not found; skip prune.")
        return []
    if not os.getenv("GITHUB_TOKEN", "").strip() and not os.getenv("GH_TOKEN", "").strip():
        log.info("GITHUB_TOKEN not set; skip prune.")
        return []
    raw = subprocess.check_output(
        [gh, "release", "list", "--repo", repo, "--limit", "100", "--json", "tagName,createdAt"],
        text=True,
    )
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def delete_tag(repo: str, tag: str) -> None:
    gh = shutil.which("gh")
    if not gh:
        return
    subprocess.run(
        [gh, "release", "delete", tag, "--repo", repo, "--yes", "--cleanup-tag"],
        check=True,
    )
    log.info("Deleted release %s", tag)


def prune(keep: int | None = None) -> list[str]:
    limit = keep_count() if keep is None else keep
    repo = github_repo()
    doomed = tags_to_delete(list_releases(repo), limit)
    for tag in doomed:
        try:
            delete_tag(repo, tag)
        except Exception:
            log.exception("Could not delete %s", tag)
    if not doomed:
        log.info("No recording releases to prune (keep %s).", limit)
    return doomed


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Delete old recording-* GitHub Releases")
    parser.add_argument("--keep", type=int)
    args = parser.parse_args()
    prune(args.keep)


if __name__ == "__main__":
    main()
