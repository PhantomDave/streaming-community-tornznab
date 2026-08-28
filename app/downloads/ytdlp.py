from __future__ import annotations

import base64
import shlex
import shutil
from pathlib import Path

from app.config import Settings
from app.models import Release

# Long enough to show the interesting prefix/suffix of a data: wrapper URL
# (see _build_av_wrapper_url) without dumping the whole base64 blob into logs.
_LOG_TOKEN_DISPLAY_LIMIT = 200


def build_output_path(settings: Settings, category: str, release_name: str) -> Path:
    return Path(settings.download_path) / category / release_name / f"{release_name}.mkv"


def build_ytdlp_command(settings: Settings, release: Release, output_path: Path) -> list[str]:
    # release.source_url already points at a single, resolution-specific HLS
    # rendition (resolved upfront in resolve_variants), not a master playlist
    # with multiple qualities. Its media playlist carries no per-format
    # RESOLUTION metadata, so a height-based selector (e.g. "bv*[height<=X]")
    # never matches and yt-dlp fails with "Requested format is not available".
    target_url = release.source_url
    if release.audio_url and release.audio_url != release.source_url:
        # vixcloud serves audio as a separate HLS rendition rather than
        # muxed into the video segments — release.source_url is video-only,
        # so feeding it to yt-dlp directly produces picture with no sound.
        # A tiny inline master playlist ties the two renditions back
        # together so yt-dlp downloads and muxes both.
        target_url = _build_av_wrapper_url(release)

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
    return command


def _build_av_wrapper_url(release: Release) -> str:
    # An inline data: URL (rather than a temp file passed via file://) means
    # no --enable-file-urls: that flag isn't scoped to just this input, it
    # also relaxes yt-dlp's guard against *any* redirect in the same run —
    # including one from the untrusted, scraped vixcloud CDN while fetching
    # source_url/audio_url themselves — resolving to a local file. data:
    # URLs carry no such risk since there's no local path to redirect into,
    # and yt-dlp/urllib support them unconditionally.
    #
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
    encoded = base64.b64encode(playlist.encode("utf-8")).decode("ascii")
    return f"data:application/vnd.apple.mpegurl;base64,{encoded}"


def command_as_string(command: list[str]) -> str:
    return " ".join(_display_token(token) for token in command)


def _display_token(token: str) -> str:
    # The AV wrapper's data: URL (see _build_av_wrapper_url) can run to a few
    # hundred characters of base64; keep the startup log line readable
    # instead of dumping the whole blob.
    quoted = shlex.quote(token)
    if len(quoted) <= _LOG_TOKEN_DISPLAY_LIMIT:
        return quoted
    return f"{quoted[:80]}...<{len(token)} chars omitted>...{quoted[-20:]}"
