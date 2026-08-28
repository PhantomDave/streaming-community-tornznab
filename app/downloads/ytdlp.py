from __future__ import annotations

import shutil
from pathlib import Path
import shlex

from app.config import Settings
from app.models import Release


def build_output_path(settings: Settings, category: str, release_name: str) -> Path:
    return Path(settings.download_path) / category / release_name / f"{release_name}.mkv"


def build_ytdlp_command(settings: Settings, release: Release, output_path: Path) -> list[str]:
    # release.source_url already points at a single, resolution-specific HLS
    # rendition (resolved upfront in resolve_variants), not a master playlist
    # with multiple qualities. Its media playlist carries no per-format
    # RESOLUTION metadata, so a height-based selector (e.g. "bv*[height<=X]")
    # never matches and yt-dlp fails with "Requested format is not available".
    command = [
        settings.ytdlp_path,
        "--no-part",
        "--newline",
        "--hls-use-mpegts",
        "-f",
        "best",
        "--concurrent-fragments",
        str(settings.download_concurrent_fragments),
    ]
    if settings.verbose_downloads:
        # Surfaces yt-dlp's own diagnostic output (invisible by default) plus
        # ffmpeg's internal log messages for AES-128 streams delegated to it —
        # ffmpeg's periodic progress stats are independent of -loglevel and
        # keep working unchanged.
        command += ["-v", "--downloader-args", "ffmpeg:-loglevel verbose"]
    # --ffmpeg-location must be an existing file/directory path; a bare command
    # name like the "ffmpeg" default isn't resolved against PATH by yt-dlp
    # itself, so only pass it once resolved to an absolute path, and otherwise
    # rely on yt-dlp's own PATH lookup.
    ffmpeg_location = shutil.which(settings.ffmpeg_path) or (
        settings.ffmpeg_path if Path(settings.ffmpeg_path).exists() else None
    )
    if ffmpeg_location:
        command += ["--ffmpeg-location", ffmpeg_location]
    command += [
        "-o",
        str(output_path.with_suffix(".%(ext)s")),
        release.source_url,
    ]
    return command


def command_as_string(command: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in command)
