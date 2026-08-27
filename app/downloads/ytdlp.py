from __future__ import annotations

from pathlib import Path
import shlex

from app.config import Settings
from app.models import Release


def build_output_path(settings: Settings, category: str, release_name: str) -> Path:
    return Path(settings.download_path) / category / release_name / f"{release_name}.mkv"


def build_ytdlp_command(settings: Settings, release: Release, output_path: Path) -> list[str]:
    quality_expr = f"bv*[height<={release.resolution}]+ba/b[height<={release.resolution}]"
    return [
        settings.ytdlp_path,
        "--no-part",
        "--newline",
        "--hls-use-mpegts",
        "-f",
        quality_expr,
        "--ffmpeg-location",
        settings.ffmpeg_path,
        "-o",
        str(output_path.with_suffix(".%(ext)s")),
        release.source_url,
    ]


def command_as_string(command: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in command)
