from app.torznab.caps import build_caps_xml


def test_caps_contains_sections() -> None:
    xml = build_caps_xml()
    assert "<caps>" in xml
    assert "movie-search" in xml
    assert "tv-search" in xml
