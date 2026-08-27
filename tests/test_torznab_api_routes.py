import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

import app.torznab.router as torznab_router
from app.config import settings
from app.db import Database
from app.deps import get_db, get_sc_client
from app.main import app
from app.models import Episode, Release, Title, Variant, now_utc


def _parse_feed(xml_payload: str) -> ET.Element:
    return ET.fromstring(xml_payload)


def _sample_release(infohash: str = "hash1") -> Release:
    return Release(
        infohash=infohash,
        sc_id=1,
        sc_type="movie",
        slug="dune",
        title="Dune",
        year=2021,
        season=None,
        episode=None,
        resolution=1080,
        audio="ITA",
        size_estimate=123456,
        release_name="Dune.2021.1080p.WEB-DL.H264.ITA-SC",
        source_url="https://example.test/master.m3u8",
        created_at=now_utc(),
    )


def test_torznab_requires_api_key_for_search() -> None:
    with TestClient(app) as client:
        response = client.get("/torznab/api", params={"t": "search", "q": "dune"})
    assert response.status_code == 401


def test_torznab_unsupported_function_returns_400() -> None:
    with TestClient(app) as client:
        response = client.get("/torznab/api", params={"t": "nope", "apikey": settings.torznab_api_key})
    assert response.status_code == 400


def test_torznab_empty_query_returns_empty_feed() -> None:
    with TestClient(app) as client:
        response = client.get("/torznab/api", params={"t": "search", "apikey": settings.torznab_api_key})
    assert response.status_code == 200
    root = _parse_feed(response.text)
    assert root.findall("./channel/item") == []


def test_torznab_search_filters_results_and_generates_torrent(tmp_path, monkeypatch) -> None:
    db = Database(str(tmp_path / "torznab.db"))

    async def fake_search_titles(_client, query: str) -> list[Title]:
        assert query == "query"
        return [
            Title(sc_id=1, slug="dune", name="Dune", sc_type="movie", year=2021),
            Title(sc_id=2, slug="breaking-bad", name="Breaking Bad", sc_type="tv"),
        ]

    async def fake_get_season_episodes(_client, sc_id: int, slug: str, season: int) -> list[Episode]:
        assert (sc_id, slug, season) == (2, "breaking-bad", 3)
        return [Episode(id=77, number=7, name="One Minute")]

    async def fake_resolve_variants(_client, *, sc_id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
        if sc_id == 1:
            assert slug == "dune"
            assert season is None
            assert episode_id is None
        else:
            assert (slug, season, episode_id) == ("breaking-bad", 3, 77)
        return [Variant(resolution=1080, bandwidth=2_800_000, url="https://cdn.example/video.m3u8", codecs="avc1.640028")]

    monkeypatch.setattr(torznab_router, "search_titles", fake_search_titles)
    monkeypatch.setattr(torznab_router, "get_season_episodes", fake_get_season_episodes)
    monkeypatch.setattr(torznab_router, "resolve_variants", fake_resolve_variants)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_sc_client] = lambda: object()
    try:
        with TestClient(app) as client:
            movie_response = client.get(
                "/torznab/api",
                params={"t": "movie", "q": "query", "apikey": settings.torznab_api_key},
            )
            tv_response = client.get(
                "/torznab/api",
                params={
                    "t": "tvsearch",
                    "q": "query",
                    "season": 3,
                    "ep": 7,
                    "apikey": settings.torznab_api_key,
                },
            )
    finally:
        app.dependency_overrides.clear()

    movie_root = _parse_feed(movie_response.text)
    movie_items = movie_root.findall("./channel/item")
    assert movie_response.status_code == 200
    assert len(movie_items) == 1
    assert movie_items[0].findtext("title") == "Dune.2021.1080p.WEB-DL.H264.ITA-SC"

    tv_root = _parse_feed(tv_response.text)
    tv_items = tv_root.findall("./channel/item")
    assert tv_response.status_code == 200
    assert len(tv_items) == 1
    assert tv_items[0].findtext("title") == "Breaking.Bad.S03E07.1080p.WEB-DL.H264.ITA-SC"

    infohash = movie_items[0].findtext("guid")
    assert infohash is not None
    stored = db.get_release(infohash)
    assert stored is not None
    assert stored.release_name == "Dune.2021.1080p.WEB-DL.H264.ITA-SC"

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            torrent_response = client.get(f"/torznab/dl/{infohash}.torrent")
    finally:
        app.dependency_overrides.clear()

    assert torrent_response.status_code == 200
    assert "application/x-bittorrent" in torrent_response.headers["content-type"]
    assert torrent_response.content == b"name=Dune.2021.1080p.WEB-DL.H264.ITA-SC&hash=" + infohash.encode("utf-8")


def test_torrent_download_returns_404_for_unknown_release(tmp_path) -> None:
    db = Database(str(tmp_path / "torznab.db"))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get("/torznab/dl/missing.torrent")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
