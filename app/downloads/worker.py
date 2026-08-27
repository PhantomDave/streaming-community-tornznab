from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re

from app.config import Settings
from app.db import Database
from app.downloads.ytdlp import build_output_path, build_ytdlp_command
from app.models import Job, Release

PROGRESS_PATTERN = re.compile(r"\[download\]\s+(?P<progress>\d+(?:\.\d+)?)%")


async def run_download_job(
    settings: Settings,
    db: Database,
    job: Job,
    release: Release,
    process_registry: dict[str, asyncio.subprocess.Process] | None = None,
) -> None:
    output_path = build_output_path(settings, job.category, release.release_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not release.source_url:
        db.update_job_state(job.id, state="error", error="Missing source URL")
        return

    command = build_ytdlp_command(settings, release, output_path)
    db.update_job_state(job.id, state="downloading", progress=0.0, bytes_done=0, bytes_total=max(release.size_estimate, 0), error="")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if process_registry is not None:
        process_registry[job.id] = process

    try:
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            progress = _parse_progress(line)
            if progress is not None:
                db.update_job_state(
                    job.id,
                    progress=progress / 100.0,
                    bytes_done=int(release.size_estimate * (progress / 100.0)),
                    bytes_total=release.size_estimate,
                )

        code = await process.wait()
    finally:
        if process_registry is not None:
            process_registry.pop(job.id, None)

    # A pause/delete request may have terminated the process on purpose; in that
    # case the job's state (or absence, if deleted) already reflects the intent
    # and must not be clobbered with a completed/error outcome.
    current = db.get_job(job.id)
    if current is None or current.state == "paused":
        return

    if code != 0:
        db.update_job_state(job.id, state="error", error=f"yt-dlp exited with code {code}")
        return

    final_path = _ensure_mkv(output_path)
    db.update_job_state(
        job.id,
        state="completed",
        progress=1.0,
        bytes_done=release.size_estimate,
        bytes_total=release.size_estimate,
        content_path=str(final_path),
        error="",
    )


def _parse_progress(line: str) -> float | None:
    match = PROGRESS_PATTERN.search(line)
    if not match:
        return None
    return float(match.group("progress"))


def _ensure_mkv(output_path: Path) -> Path:
    if output_path.exists():
        return output_path
    for extension in (".mp4", ".mkv", ".ts", ".webm"):
        candidate = output_path.with_suffix(extension)
        if candidate.exists():
            if extension != ".mkv":
                mkv_target = output_path
                os.replace(candidate, mkv_target)
                return mkv_target
            return candidate
    output_path.touch(exist_ok=True)
    return output_path
