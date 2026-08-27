from app.config import Settings
from app.downloads.ytdlp import build_ytdlp_command
from app.models import Release, now_utc


def _release(**overrides) -> Release:
    defaults = dict(
        infohash="abc123",
        sc_id=1,
        sc_type="movie",
        slug="dune",
        title="Dune",
        year=2021,
        season=None,
        episode=None,
        resolution=1080,
        audio="ITA",
        size_estimate=1024,
        release_name="Dune.2021.1080p.WEB-DL.H264.ITA-SC",
        source_url="https://vixcloud.co/playlist/1?rendition=1080p",
        created_at=now_utc(),
    )
    defaults.update(overrides)
    return Release(**defaults)


def test_build_ytdlp_command_uses_best_format_not_height_filter(tmp_path) -> None:
    # release.source_url already points at a single, resolution-specific HLS
    # rendition whose media playlist carries no per-format height metadata, so
    # a height-based selector like "bv*[height<=1080]" always fails with
    # "Requested format is not available" against a real stream.
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere")
    release = _release()
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "-f" in command
    format_index = command.index("-f")
    assert command[format_index + 1] == "best"
    assert not any("height" in token for token in command)


def test_build_ytdlp_command_omits_unresolvable_ffmpeg_location(tmp_path) -> None:
    # yt-dlp does not resolve a bare command name against PATH for
    # --ffmpeg-location (unlike its own internal ffmpeg lookup), so passing
    # the literal default "ffmpeg" makes yt-dlp warn "does not exist" and
    # continue without ffmpeg. The flag must be omitted instead so yt-dlp
    # falls back to its own PATH search.
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere")
    release = _release()
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "--ffmpeg-location" not in command


def test_build_ytdlp_command_includes_resolvable_ffmpeg_location(tmp_path) -> None:
    ffmpeg_stub = tmp_path / "ffmpeg"
    ffmpeg_stub.write_text("#!/bin/sh\n")
    ffmpeg_stub.chmod(0o755)
    settings = Settings(FFMPEG_PATH=str(ffmpeg_stub))
    release = _release()
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "--ffmpeg-location" in command
    location_index = command.index("--ffmpeg-location")
    assert command[location_index + 1] == str(ffmpeg_stub)
