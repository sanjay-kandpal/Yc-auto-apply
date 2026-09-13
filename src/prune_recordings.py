from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from config_loader import load_config
from log_config import setup_logging
from publish_release import TAG_PREFIX, github_repo

load_dotenv()
log = logging.getLogger(__name__)


def retain_hours() -> int:
    return int((load_config().get("spectate") or {}).get("retain_hours") or 24)


def keep_count() -> int:
    return int((load_config().get("spectate") or {}).get("keep_releases") or 30)


def _parse_created(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def tags_to_delete(
    entries: list[dict],
    retain_hours: int,
    keep: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    tagged = [item for item in entries if str(item.get("tagName") or "").startswith(TAG_PREFIX)]
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(hours=max(0, retain_hours))
    doomed: set[str] = set()
    for item in tagged:
        created = _parse_created(item.get("createdAt"))
        if created is not None and created < cutoff:
            doomed.add(str(item["tagName"]))
    if keep is not None and keep >= 0:
        tagged.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        for item in tagged[keep:]:
            doomed.add(str(item["tagName"]))
    return sorted(doomed)


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


def prune(hours: int | None = None, keep: int | None = None) -> list[str]:
    age = retain_hours() if hours is None else hours
    cap = keep_count() if keep is None else keep
    repo = github_repo()
    doomed = tags_to_delete(list_releases(repo), retain_hours=age, keep=cap)
    for tag in doomed:
        try:
            delete_tag(repo, tag)
        except Exception:
            log.exception("Could not delete %s", tag)
    if not doomed:
        log.info("No recording releases to prune (retain %sh, keep %s).", age, cap)
    else:
        log.info("Pruned %s recording release(s) (retain %sh, keep %s).", len(doomed), age, cap)
    return doomed


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Delete recording-* GitHub Releases older than retain_hours")
    parser.add_argument("--retain-hours", type=int)
    parser.add_argument("--keep", type=int)
    args = parser.parse_args()
    prune(hours=args.retain_hours, keep=args.keep)


if __name__ == "__main__":
    main()
