import base64

from app.config import Settings
from app.downloads.ytdlp import build_ytdlp_command, command_as_string
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


def test_build_ytdlp_command_uses_bv_ba_format_not_height_filter(tmp_path) -> None:
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
    assert command[format_index + 1] == "bv+ba/b"
    assert not any("height" in token for token in command)
    assert command[-1] == release.source_url


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


def test_build_ytdlp_command_includes_verbose_flags_by_default(tmp_path) -> None:
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere")
    release = _release()
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "-v" in command
    assert "--downloader-args" in command
    args_index = command.index("--downloader-args")
    assert command[args_index + 1] == "ffmpeg:-loglevel verbose"


def test_build_ytdlp_command_omits_verbose_flags_when_disabled(tmp_path) -> None:
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere", VERBOSE_DOWNLOADS=False)
    release = _release()
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "-v" not in command
    assert "--downloader-args" not in command


def test_build_ytdlp_command_includes_concurrent_fragments(tmp_path) -> None:
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere", DOWNLOAD_CONCURRENT_FRAGMENTS=8)
    release = _release()
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "--concurrent-fragments" in command
    index = command.index("--concurrent-fragments")
    assert command[index + 1] == "8"


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


def test_build_ytdlp_command_wraps_separate_audio_rendition_as_data_url(tmp_path) -> None:
    # vixcloud commonly serves audio as its own HLS rendition rather than
    # muxed into the video segments; source_url alone is picture-only, so
    # yt-dlp needs a small inline playlist tying it back to audio_url. This
    # is passed as a data: URL rather than a temp file behind file:// so no
    # --enable-file-urls (and its process-wide relaxation of yt-dlp's
    # local-file redirect guard) is needed.
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere")
    release = _release(audio_url="https://vixcloud.co/playlist/1?type=audio&rendition=ita")
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "--enable-file-urls" not in command
    target_url = command[-1]
    assert target_url.startswith("data:application/vnd.apple.mpegurl;base64,")

    encoded = target_url.split(",", 1)[1]
    wrapper_text = base64.b64decode(encoded).decode("utf-8")
    assert "#EXT-X-MEDIA:TYPE=AUDIO" in wrapper_text
    assert release.audio_url in wrapper_text
    assert release.source_url in wrapper_text


def test_build_ytdlp_command_skips_wrapper_when_audio_matches_source(tmp_path) -> None:
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere")
    release = _release(audio_url="https://vixcloud.co/playlist/1?rendition=1080p")
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)

    assert "--enable-file-urls" not in command
    assert command[-1] == release.source_url


def test_command_as_string_truncates_long_data_url(tmp_path) -> None:
    # The AV wrapper's data: URL can run to a few hundred base64 characters;
    # the logged command line should stay readable rather than dumping it all.
    settings = Settings(FFMPEG_PATH="ffmpeg-does-not-exist-anywhere")
    release = _release(audio_url="https://vixcloud.co/playlist/1?type=audio&rendition=ita")
    output_path = tmp_path / "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    command = build_ytdlp_command(settings, release, output_path)
    rendered = command_as_string(command)

    assert "...<" in rendered
    assert "chars omitted>..." in rendered
    assert len(rendered) < len(" ".join(command))
