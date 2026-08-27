from app.downloads.worker import _parse_ffmpeg_duration, _parse_ffmpeg_progress


def test_parse_ffmpeg_duration_extracts_seconds() -> None:
    line = "Duration: 02:27:56.75, start: 0.083333, bitrate: N/A"
    assert _parse_ffmpeg_duration(line) == 2 * 3600 + 27 * 60 + 56.75


def test_parse_ffmpeg_duration_returns_none_when_absent() -> None:
    assert _parse_ffmpeg_duration("Stream #0:0: Video: h264") is None


def test_parse_ffmpeg_progress_computes_percentage() -> None:
    duration = 100.0
    line = "frame=  100 fps=25 q=-1.0 size=256KiB time=00:00:25.00 bitrate=100kbits/s"
    progress = _parse_ffmpeg_progress(line, duration)
    assert progress == 25.0


def test_parse_ffmpeg_progress_takes_last_match_when_line_has_several(
) -> None:
    # ffmpeg repaints its progress stats with '\r' instead of '\n'; when the
    # subprocess reader only splits on '\n', several updates can land
    # concatenated in a single line. The most recent one must win.
    duration = 100.0
    line = "time=00:00:10.00 ... time=00:00:20.00 ... time=00:00:30.00"
    progress = _parse_ffmpeg_progress(line, duration)
    assert progress == 30.0


def test_parse_ffmpeg_progress_clamps_to_99_9_near_completion() -> None:
    duration = 100.0
    line = "time=00:01:50.00"
    progress = _parse_ffmpeg_progress(line, duration)
    assert progress == 99.9


def test_parse_ffmpeg_progress_returns_none_without_time() -> None:
    assert _parse_ffmpeg_progress("no progress info here", 100.0) is None


def test_parse_ffmpeg_progress_returns_none_for_non_positive_duration() -> None:
    # Duration can legitimately be reported as 0.0 for a malformed/degenerate
    # stream; this must not raise (division by zero) and must not be treated
    # as "no duration known" upstream (a falsy-but-not-None 0.0).
    line = "time=00:00:10.00"
    assert _parse_ffmpeg_progress(line, 0.0) is None
