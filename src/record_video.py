from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config_loader import load_config
from log_config import setup_logging

log = logging.getLogger(__name__)

OBJECT_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/(scan|submit)-[A-Za-z0-9._-]+\.mp4$")
SAFE_RUN_ID = re.compile(r"[^A-Za-z0-9._-]+")


def recording_enabled() -> bool:
    return os.getenv("RECORD_RUN", "").strip().lower() in ("1", "true", "yes")


def recording_dir() -> Path:
    raw = os.getenv("RECORDING_DIR", "").strip()
    if raw:
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        return path
    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(os.getenv("RUNNER_TEMP") or "data/recordings") / str(run_id)
    if not path.is_absolute():
        from config_loader import ROOT

        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def video_size() -> tuple[int, int]:
    cfg = load_config().get("spectate") or {}
    size = cfg.get("record_video_size") or [1280, 720]
    return int(size[0]), int(size[1])


def workflow_slug(name: str | None = None) -> str:
    text = (name or os.getenv("SPECTATE_WORKFLOW") or os.getenv("GITHUB_WORKFLOW") or "scan").lower()
    return "submit" if "submit" in text else "scan"


def build_object_key(workflow: str | None = None, run_id: str | None = None, day: str | None = None) -> str:
    day_s = day or datetime.now(timezone.utc).date().isoformat()
    safe_id = SAFE_RUN_ID.sub("", str(run_id or os.getenv("GITHUB_RUN_ID") or "local")) or "local"
    return f"{day_s}/{workflow_slug(workflow)}-{safe_id}.mp4"


def object_key_allowed(key: str) -> bool:
    return bool(OBJECT_KEY_RE.match((key or "").strip()))


def attach_page_tracker(context) -> list:
    pages: list = []

    def on_page(page) -> None:
        pages.append(page)

    context.on("page", on_page)
    pages.extend(context.pages)
    return pages


def finalize_recordings(pages: list, dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ordered: list[Path] = []
    for page in pages:
        try:
            video = getattr(page, "video", None)
            if not video:
                continue
            src = Path(video.path())
            if src.exists():
                ordered.append(src)
        except Exception as exc:
            log.warning("Skip page video: %s", exc)
    clips: list[Path] = []
    for i, src in enumerate(ordered, 1):
        dest = dest_dir / f"clip-{i:02d}.webm"
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
            if src.parent.resolve() == dest_dir.resolve() and not src.name.startswith("clip-"):
                src.unlink(missing_ok=True)
        clips.append(dest)
    manifest = {"clips": [c.name for c in clips], "count": len(clips)}
    (dest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("Recording manifest: %s clip(s) in %s", len(clips), dest_dir)
    return clips


def clips_from_dir(raw_dir: Path) -> list[Path]:
    manifest_path = raw_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        clips = [raw_dir / name for name in data.get("clips") or [] if (raw_dir / str(name)).exists()]
        if clips:
            return clips
    numbered = sorted(raw_dir.glob("clip-*.webm"))
    if numbered:
        return numbered
    return sorted(raw_dir.glob("*.webm"))


def _encode_args(output: Path) -> list[str]:
    cfg = load_config().get("spectate") or {}
    crf = int(cfg.get("crf", 32))
    maxrate = int(cfg.get("maxrate_k", 800))
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-maxrate",
        f"{maxrate}k",
        "-bufsize",
        f"{maxrate * 2}k",
        "-an",
        "-movflags",
        "+faststart",
        str(output),
    ]


def _scale_filter() -> str:
    width, height = video_size()
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=15,setsar=1,format=yuv420p"
    )


def merge_clips(raw_dir: Path, out_dir: Path, key: str) -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("ffmpeg not found; skip merge.")
        return None
    clips = clips_from_dir(raw_dir)
    if not clips:
        log.info("No .webm clips in %s", raw_dir)
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / Path(key).name
    vf = _scale_filter()
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if len(clips) == 1:
        cmd += ["-i", str(clips[0]), "-vf", vf]
    else:
        for clip in clips:
            cmd += ["-i", str(clip)]
        parts = []
        labels = []
        for i in range(len(clips)):
            parts.append(f"[{i}:v]{vf}[v{i}]")
            labels.append(f"[v{i}]")
        parts.append(f"{''.join(labels)}concat=n={len(clips)}:v=1:a=0[v]")
        cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    cmd += _encode_args(output)
    log.info("Merging %s clip(s) -> %s", len(clips), output)
    subprocess.run(cmd, check=True)
    log.info("Merged recording %s bytes=%s", output.name, output.stat().st_size)
    return output


def write_meta(out_dir: Path, key: str | None, path: Path | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "key": key if path else None,
        "path": str(path) if path else None,
        "bytes": path.stat().st_size if path and path.exists() else 0,
    }
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    key_path = out_dir / "key.txt"
    key_path.write_text(meta["key"] or "", encoding="utf-8")
    github_out = os.getenv("GITHUB_OUTPUT", "").strip()
    if github_out:
        with open(github_out, "a", encoding="utf-8") as handle:
            handle.write(f"key={meta['key'] or ''}\n")
            handle.write(f"bytes={meta['bytes']}\n")
    return meta_path


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Merge Playwright .webm clips into one mp4")
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--workflow")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    key = build_object_key(args.workflow, args.run_id)
    path = None
    try:
        path = merge_clips(raw_dir, out_dir, key)
    except Exception:
        log.exception("Merge failed")
    write_meta(out_dir, key, path)
    log.info("Spectate key=%s file=%s", key if path else "", path or "")


if __name__ == "__main__":
    main()
