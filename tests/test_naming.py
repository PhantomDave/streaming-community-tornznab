from app.models import Title
from app.torznab.naming import audio_label, build_release_name


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


def test_build_movie_release_name_omits_missing_year() -> None:
    name = build_release_name(
        title=Title(sc_id=3, slug="nosferatu", name="Nosferatu", sc_type="movie"),
        resolution=1080,
        codecs="avc1.640028",
        audio="eng",
        season=None,
        episode=None,
        release_group="SC",
    )
    assert "Nosferatu.1080p.WEB-DL.H264.ENG-SC" == name


def test_build_release_name_normalizes_special_characters() -> None:
    name = build_release_name(
        title=Title(sc_id=4, slug="spider-man", name=" Spider-Man: No Way Home / Extended ", sc_type="movie", year=2021),
        resolution=1080,
        codecs="avc1.640028",
        audio="ita",
        season=None,
        episode=None,
        release_group="SC",
    )
    assert "Spider-Man.No.Way.Home.Extended.2021.1080p.WEB-DL.H264.ITA-SC" == name


def test_audio_label_ita() -> None:
    assert audio_label("ita") == "ITA"


def test_audio_label_eng() -> None:
    assert audio_label("eng") == "ENG"


def test_audio_label_multi() -> None:
    assert audio_label("ita,eng") == "MULTi"


def test_audio_label_japanese_only_is_not_ita() -> None:
    assert audio_label("jpn") == "SUB-ITA"
    assert audio_label("jap") == "SUB-ITA"


def test_audio_label_unknown_language_is_not_ita() -> None:
    assert audio_label("fre") == "SUB-ITA"
    assert audio_label("") == "SUB-ITA"


def test_build_release_name_japanese_only_audio_is_not_ita() -> None:
    name = build_release_name(
        title=Title(sc_id=5, slug="some-anime", name="Some Anime", sc_type="tv"),
        resolution=1080,
        codecs="avc1.640028",
        audio="jpn",
        season=1,
        episode=1,
        release_group="SC",
    )
    assert "Some.Anime.S01E01.1080p.WEB-DL.H264.SUB-ITA-SC" == name
