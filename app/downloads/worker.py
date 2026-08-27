from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import re

from app.config import Settings
from app.db import Database
from app.downloads.ytdlp import build_output_path, build_ytdlp_command, command_as_string
from app.models import Job, Release

PROGRESS_PATTERN = re.compile(r"\[download\]\s+(?P<progress>\d+(?:\.\d+)?)%")
# When the HLS stream is AES-128 encrypted, yt-dlp delegates the actual
# download to ffmpeg (see the "extraction will be delegated to ffmpeg"
# warning), which never prints "[download] X%" lines. Its own progress shows
# up instead as "time=HH:MM:SS.ss" against the input duration reported
# earlier as "Duration: HH:MM:SS.ss". ffmpeg repaints that line with '\r'
# instead of '\n', so several updates can land in a single line read here —
# take the last match to get the most recent value.
DURATION_PATTERN = re.compile(r"Duration:\s*(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")
FFMPEG_TIME_PATTERN = re.compile(r"time=(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")
_LOG_PROGRESS_STEP = 10.0

logger = logging.getLogger(__name__)


async def run_download_job(settings: Settings, db: Database, job: Job, release: Release) -> None:
    output_path = build_output_path(settings, job.category, release.release_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not release.source_url:
        logger.error("Job id=%s: release %s has no source_url, cannot download", job.id, release.release_name)
        db.update_job_state(job.id, state="error", error="Missing source URL")
        return

    command = build_ytdlp_command(settings, release, output_path)
    logger.info("Job id=%s: starting yt-dlp: %s", job.id, command_as_string(command))
    db.update_job_state(job.id, state="downloading", progress=0.0, bytes_done=0, bytes_total=max(release.size_estimate, 0), error="")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        logger.error("Job id=%s: failed to launch yt-dlp (%s): %s", job.id, settings.ytdlp_path, exc)
        db.update_job_state(job.id, state="error", error=f"yt-dlp not found: {exc}")
        return

    assert process.stdout is not None
    last_logged_progress = -1.0
    duration_seconds: float | None = None
    output_lines: list[str] = []
    async for raw_line in process.stdout:
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if line:
            output_lines.append(line)
            if len(output_lines) > 50:
                output_lines.pop(0)

        progress = _parse_progress(line)
        if progress is None and duration_seconds is None:
            duration_seconds = _parse_ffmpeg_duration(line)
            if duration_seconds is not None:
                logger.debug("Job id=%s: ffmpeg input duration is %.1fs", job.id, duration_seconds)
        if progress is None and duration_seconds:
            progress = _parse_ffmpeg_progress(line, duration_seconds)

        if progress is not None:
            db.update_job_state(
                job.id,
                progress=progress / 100.0,
                bytes_done=int(release.size_estimate * (progress / 100.0)),
                bytes_total=release.size_estimate,
            )
            if progress - last_logged_progress >= _LOG_PROGRESS_STEP or progress >= 100.0:
                logger.info("Job id=%s: progress %.1f%%", job.id, progress)
                last_logged_progress = progress
        elif line:
            logger.debug("Job id=%s: yt-dlp: %s", job.id, line)

    code = await process.wait()
    if code != 0:
        tail = " | ".join(output_lines[-10:])
        logger.error("Job id=%s: yt-dlp exited with code %d. Last output: %s", job.id, code, tail)
        db.update_job_state(job.id, state="error", error=f"yt-dlp exited with code {code}")
        return

    final_path = _ensure_mkv(output_path)
    logger.info("Job id=%s: download completed, final file at %s", job.id, final_path)
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


def _parse_ffmpeg_duration(line: str) -> float | None:
    match = DURATION_PATTERN.search(line)
    if not match:
        return None
    return _hms_to_seconds(match)


def _parse_ffmpeg_progress(line: str, duration_seconds: float) -> float | None:
    matches = list(FFMPEG_TIME_PATTERN.finditer(line))
    if not matches or duration_seconds <= 0:
        return None
    elapsed = _hms_to_seconds(matches[-1])
    return max(0.0, min(elapsed / duration_seconds * 100.0, 99.9))


def _hms_to_seconds(match: re.Match[str]) -> float:
    return int(match.group("h")) * 3600 + int(match.group("m")) * 60 + float(match.group("s"))


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
