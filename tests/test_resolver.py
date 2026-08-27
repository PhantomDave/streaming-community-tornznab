from app.sc.resolver import _extract_m3u8_url, _parse_master_playlist


def test_extract_m3u8_url() -> None:
    html = '<html><script>var u="https://cdn.example/video/master.m3u8?token=123";</script></html>'
    assert _extract_m3u8_url(html) == "https://cdn.example/video/master.m3u8?token=123"


def test_parse_master_playlist_sorts_variants() -> None:
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,CODECS="avc1.640029,mp4a.40.2"
v720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=3840x2160,CODECS="hev1.1.6.L93.B0,mp4a.40.2"
v2160.m3u8
"""
    variants = _parse_master_playlist(playlist, "https://cdn.example/master.m3u8")
    assert [variant.resolution for variant in variants] == [2160, 720]
    assert variants[0].url == "https://cdn.example/v2160.m3u8"
    assert variants[1].bandwidth == 2800000
