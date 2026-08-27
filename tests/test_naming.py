from app.models import Title
from app.torznab.naming import build_release_name


def test_build_movie_release_name() -> None:
    name = build_release_name(
        title=Title(sc_id=1, slug="dune", name="Dune", sc_type="movie", year=2021),
        resolution=1080,
        codecs="avc1.640028",
        audio="ita",
        season=None,
        episode=None,
        release_group="SC",
    )
    assert "Dune.2021.1080p.WEB-DL.H264.ITA-SC" == name


def test_build_tv_release_name() -> None:
    name = build_release_name(
        title=Title(sc_id=2, slug="breaking-bad", name="Breaking Bad", sc_type="tv"),
        resolution=720,
        codecs="hev1.1",
        audio="ita,eng",
        season=3,
        episode=7,
        release_group="SC",
    )
    assert "Breaking.Bad.S03E07.720p.WEB-DL.H265.MULTi-SC" == name
