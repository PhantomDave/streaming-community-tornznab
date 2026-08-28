from __future__ import annotations

import shlex
import shutil
import tempfile
from pathlib import Path

from app.config import Settings
from app.models import Release


def build_output_path(settings: Settings, category: str, release_name: str) -> Path:
    return Path(settings.download_path) / category / release_name / f"{release_name}.mkv"


def build_ytdlp_command(settings: Settings, release: Release, output_path: Path) -> tuple[list[str], Path | None]:
    # release.source_url already points at a single, resolution-specific HLS
    # rendition (resolved upfront in resolve_variants), not a master playlist
    # with multiple qualities. Its media playlist carries no per-format
    # RESOLUTION metadata, so a height-based selector (e.g. "bv*[height<=X]")
    # never matches and yt-dlp fails with "Requested format is not available".
    target_url = release.source_url
    wrapper_path: Path | None = None
    if release.audio_url and release.audio_url != release.source_url:
        # vixcloud serves audio as a separate HLS rendition rather than
        # muxed into the video segments — release.source_url is video-only,
        # so feeding it to yt-dlp directly produces picture with no sound.
        # A tiny local master playlist ties the two renditions back together
        # so yt-dlp downloads and muxes both.
        wrapper_path = _write_av_wrapper(release)
        target_url = wrapper_path.as_uri()

    command = [
        settings.ytdlp_path,
        "--no-part",
        "--newline",
        "--hls-use-mpegts",
        "-f",
        "bv+ba/b",
        "--concurrent-fragments",
        str(settings.download_concurrent_fragments),
    ]
    if wrapper_path is not None:
        # yt-dlp refuses file:// input by default (SSRF hardening against a
        # *remote* page redirecting it to a local file); safe here since we
        # authored the wrapper ourselves and it only ever points back out to
        # the two https:// rendition URLs already being downloaded.
        command.append("--enable-file-urls")
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
        target_url,
    ]
    return command, wrapper_path


def _write_av_wrapper(release: Release) -> Path:
    # CODECS naming just the video codec (or the full "video,audio" list
    # copied from the source master — either works) is what tells yt-dlp
    # this EXT-X-STREAM-INF is video-only rather than pre-merged, so it
    # actually pulls in the AUDIO= group instead of ignoring it.
    codecs = f'CODECS="{release.codecs}",' if release.codecs else ""
    playlist = (
        "#EXTM3U\n"
        f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="{release.audio or "audio"}",'
        f'DEFAULT=YES,AUTOSELECT=YES,URI="{release.audio_url}"\n'
        f'#EXT-X-STREAM-INF:BANDWIDTH=1,{codecs}AUDIO="audio"\n'
        f"{release.source_url}\n"
    )
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".m3u8", prefix="sctorznab-av-", delete=False, encoding="utf-8"
    )
    with handle:
        handle.write(playlist)
    return Path(handle.name)


def command_as_string(command: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in command)
