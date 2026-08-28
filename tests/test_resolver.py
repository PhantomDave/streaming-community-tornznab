from app.sc.resolver import _extract_m3u8_url, _parse_master_playlist, settings

# Trimmed to the two audio renditions and one video variant actually seen on
# a live vixcloud playlist — audio is a wholly separate rendition (own URI,
# own segments), referenced from EXT-X-STREAM-INF only via AUDIO="audio".
_PLAYLIST_WITH_SEPARATE_AUDIO = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Italian",DEFAULT=YES,AUTOSELECT=YES,LANGUAGE="ita",URI="https://vixcloud.co/playlist/1?type=audio&rendition=ita"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",DEFAULT=NO,AUTOSELECT=NO,LANGUAGE="eng",URI="https://vixcloud.co/playlist/1?type=audio&rendition=eng"
#EXT-X-STREAM-INF:BANDWIDTH=2150000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1280x720,AUDIO="audio"
https://vixcloud.co/playlist/1?type=video&rendition=720p
"""


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


def test_extract_m3u8_url_returns_none_when_missing() -> None:
    assert _extract_m3u8_url("<html></html>") is None


def test_parse_master_playlist_skips_missing_resolution_and_keeps_missing_bandwidth() -> None:
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:CODECS="avc1.640029"
ignored.m3u8
#EXT-X-STREAM-INF:RESOLUTION=1280x720,CODECS="avc1.640029"
v720.m3u8
"""
    variants = _parse_master_playlist(playlist, "https://cdn.example/master.m3u8")
    assert [variant.resolution for variant in variants] == [720]
    assert variants[0].url == "https://cdn.example/v720.m3u8"
    assert variants[0].bandwidth is None


def test_parse_master_playlist_resolves_separate_audio_rendition() -> None:
    # A STREAM-INF's own CODECS list still names the audio codec even when
    # the audio itself lives at a completely different URL (delivered via a
    # same-GROUP-ID EXT-X-MEDIA rendition, as vixcloud does) — variant.url
    # alone is video-only, so downloading just that leaves the file silent.
    variants = _parse_master_playlist(_PLAYLIST_WITH_SEPARATE_AUDIO, "https://vixcloud.co/master.m3u8")
    assert len(variants) == 1
    variant = variants[0]
    assert variant.url == "https://vixcloud.co/playlist/1?type=video&rendition=720p"
    # settings.preferred_audio_list defaults to ["ita", "eng"], so the
    # Italian rendition (also DEFAULT=YES) wins.
    assert variant.audio_url == "https://vixcloud.co/playlist/1?type=audio&rendition=ita"
    assert variant.audio == "ITA"


def test_parse_master_playlist_honors_preferred_audio_language(monkeypatch) -> None:
    monkeypatch.setattr(settings, "preferred_audio_list", ["eng"])
    variants = _parse_master_playlist(_PLAYLIST_WITH_SEPARATE_AUDIO, "https://vixcloud.co/master.m3u8")
    assert variants[0].audio_url == "https://vixcloud.co/playlist/1?type=audio&rendition=eng"
    assert variants[0].audio == "ENG"


def test_parse_master_playlist_variant_without_audio_group_has_no_audio_url() -> None:
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,CODECS="avc1.640029,mp4a.40.2"
v720.m3u8
"""
    variants = _parse_master_playlist(playlist, "https://cdn.example/master.m3u8")
    assert variants[0].audio_url == ""
    assert variants[0].audio == "ITA"
