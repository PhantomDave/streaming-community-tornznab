from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import time

from typing import TYPE_CHECKING

from app.config import Settings
from app.db import Database
from app.downloads.ytdlp import build_output_path, build_ytdlp_command, command_as_string
from app.models import Job, Release

if TYPE_CHECKING:
    from app.downloads.manager import SpeedTracker

PROGRESS_PATTERN = re.compile(r"\[download\]\s+(?P<progress>\d+(?:\.\d+)?)%")
# When the HLS stream is AES-128 encrypted, yt-dlp delegates the actual
# download to ffmpeg (see the "extraction will be delegated to ffmpeg"
# warning), which never prints "[download] X%" lines. Its own progress shows
# up instead as "time=HH:MM:SS.ss" against the input duration reported
# earlier as "Duration: HH:MM:SS.ss". ffmpeg repaints that line with '\r'
# instead of '\n' — the raw-chunk reader below splits on either, but a chunk
# can still land mid-repaint with more than one update, so take the last
# match to get the most recent value.
DURATION_PATTERN = re.compile(r"Duration:\s*(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")
FFMPEG_TIME_PATTERN = re.compile(r"time=(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")
_LOG_PROGRESS_STEP = 10.0

# Failures whose message indicates yt-dlp will never succeed on retry, no
# matter how many times we re-run it (unlike a stall or a transient network
# hiccup, which are worth retrying indefinitely).
_PERMANENT_FAILURE_SIGNALS = (
    "unsupported url",
    "requested format is not available",
    "http error 404",
    "http error 403",
    "this video is unavailable",
    "unable to extract",
    "drm protected",
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_ytdlp_failure(output_lines: list[str]) -> str:
    tail = " ".join(output_lines[-15:]).lower()
    if any(signal in tail for signal in _PERMANENT_FAILURE_SIGNALS):
        return "permanent"
    return "transient"


async def run_download_job(
    settings: Settings,
    db: Database,
    job: Job,
    release: Release,
    process_registry: dict[str, asyncio.subprocess.Process] | None = None,
    speed_tracker: "SpeedTracker | None" = None,
    *,
    attempt: int = 1,
    max_attempts: int = 1,
) -> None:
    output_path = build_output_path(settings, job.category, release.release_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not release.source_url:
        logger.error("Job id=%s: release %s has no source_url, cannot download", job.id, release.release_name)
        db.record_job_failure(job.id, error="Missing source URL", error_kind="permanent")
        return

    command = build_ytdlp_command(settings, release, output_path)
    logger.info(
        "Job id=%s infohash=%s: starting yt-dlp (attempt %d/%d): %s",
        job.id, job.infohash, attempt, max_attempts, command_as_string(command),
    )
    db.update_job_state(job.id, state="downloading", progress=0.0, bytes_done=0, bytes_total=max(release.size_estimate, 0), error="", error_kind="")
    if speed_tracker is not None:
        speed_tracker.reset(job.id)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        logger.error("Job id=%s: failed to launch yt-dlp (%s): %s", job.id, settings.ytdlp_path, exc)
        db.record_job_failure(job.id, error=f"yt-dlp not found: {exc}", error_kind="permanent")
        return

    if process_registry is not None:
        process_registry[job.id] = process

    stalled = False
    try:
        assert process.stdout is not None
        last_logged_progress = -1.0
        last_progress_value = -1.0
        last_progress_time = time.monotonic()
        duration_seconds: float | None = None
        output_lines: list[str] = []
        buffer = b""
        lines_seen = 0
        poll_interval = min(settings.download_progress_poll_interval, settings.download_stall_timeout)
        while True:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(4096), timeout=poll_interval)
            except asyncio.TimeoutError:
                chunk = b""
            else:
                if not chunk:
                    break

            if chunk:
                raw_lines, buffer = _extract_lines(buffer + chunk)
                for raw_line in raw_lines:
                    lines_seen += 1
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    output_lines.append(line)
                    if len(output_lines) > 50:
                        output_lines.pop(0)

                    progress = _parse_progress(line)
                    if progress is None and duration_seconds is None:
                        duration_seconds = _parse_ffmpeg_duration(line)
                        if duration_seconds is not None:
                            logger.debug("Job id=%s: ffmpeg input duration is %.1fs", job.id, duration_seconds)
                    if progress is None and duration_seconds is not None:
                        progress = _parse_ffmpeg_progress(line, duration_seconds)

                    if progress is not None:
                        bytes_done = int(release.size_estimate * (progress / 100.0))
                        db.update_job_state(
                            job.id,
                            progress=progress / 100.0,
                            bytes_done=bytes_done,
                            bytes_total=release.size_estimate,
                        )
                        if speed_tracker is not None:
                            speed_tracker.record(job.id, bytes_done)
                        if progress - last_logged_progress >= _LOG_PROGRESS_STEP or progress >= 100.0:
                            logger.info("Job id=%s: progress %.1f%%", job.id, progress)
                            last_logged_progress = progress
                        if progress != last_progress_value or last_progress_value < 0:
                            # Any change counts as real forward progress, not
                            # just an increase: when a release needs separate
                            # video/audio HLS tracks (bv+ba), yt-dlp finishes
                            # one file at 100% and then starts the next from
                            # 0%, so percentage legitimately drops between
                            # formats. A repeated/non-advancing value (e.g. an
                            # ffmpeg time= line stuck on the same timestamp)
                            # still must NOT reset the stall clock, or a frozen
                            # process that keeps emitting output would never
                            # be detected as stalled.
                            last_progress_value = progress
                            last_progress_time = time.monotonic()
                            db.update_job_state(job.id, last_progress_at=_now_iso())
                    elif line:
                        level = logging.INFO if settings.verbose_downloads else logging.DEBUG
                        logger.log(level, "Job id=%s: yt-dlp: %s", job.id, line)
                    if last_progress_value < 0:
                        # No progress signal has ever been parsed yet in this
                        # run (still in yt-dlp's startup/resolving phase, e.g.
                        # before ffmpeg has even printed Duration:) — tolerate
                        # any byte activity as a sign of life until the first
                        # real progress figure arrives.
                        last_progress_time = time.monotonic()

            if time.monotonic() - last_progress_time >= settings.download_stall_timeout:
                logger.error(
                    "Job id=%s infohash=%s release=%s: STALL DETECTED - no progress advance for "
                    "%.0fs (last known %.1f%%, %d lines seen, attempt %d/%d), killing process",
                    job.id, job.infohash, release.release_name,
                    time.monotonic() - last_progress_time,
                    max(last_progress_value, 0.0),
                    lines_seen, attempt, max_attempts,
                )
                stalled = True
                process.kill()
                break

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

    if stalled:
        logger.error("Job id=%s: killed after stalling with no progress advance", job.id)
        db.record_job_failure(
            job.id,
            error=f"Download stalled: no progress for {settings.download_stall_timeout:.0f}s",
            error_kind="transient",
        )
        return

    if code != 0:
        tail = " | ".join(output_lines[-10:])
        kind = _classify_ytdlp_failure(output_lines)
        logger.error("Job id=%s: yt-dlp exited with code %d (error_kind=%s). Last output: %s", job.id, code, kind, tail)
        db.record_job_failure(job.id, error=f"yt-dlp exited with code {code}", error_kind=kind)
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


def _extract_lines(buffer: bytes) -> tuple[list[bytes], bytes]:
    # ffmpeg-delegated downloads repaint their progress line with '\r' rather
    # than '\n' for long stretches, so splitting on '\n' alone (as
    # readline() does) can leave the reader waiting well past a real '\r'
    # update and misdetect an actively-progressing download as stalled.
    # Splitting raw bytes on either delimiter keeps every update visible.
    *lines, remainder = re.split(rb"[\r\n]", buffer)
    return lines, remainder


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
