import xml.etree.ElementTree as ET
from collections.abc import Callable

from fastapi.testclient import TestClient

from app.config import settings
from app.db import Database
from app.deps import get_db, get_provider_registry
from app.main import app
from app.models import Episode, Release, Title, Variant, now_utc
from app.provider import Provider, ProviderRegistry


def _parse_feed(xml_payload: str) -> ET.Element:
    return ET.fromstring(xml_payload)


class FakeProvider(Provider):
    def __init__(
        self,
        *,
        source: str = "sc",
        search: Callable[[str], list[Title]] | None = None,
        episodes: Callable[[int, str, int], list[Episode]] | None = None,
        variants: Callable[[int, str, int | None, int | None], list[Variant]] | None = None,
    ) -> None:
        self.source = source
        self._search = search or (lambda query: [])
        self._episodes = episodes or (lambda id, slug, season: [])
        self._variants = variants or (lambda id, slug, season, episode_id: [])

    async def search_titles(self, query: str) -> list[Title]:
        return self._search(query)

    async def get_title_details(self, id: int, slug: str) -> dict:
        return {}

    async def get_season_episodes(self, id: int, slug: str, season: int) -> list[Episode]:
        return self._episodes(id, slug, season)

    async def resolve_variants(self, id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
        return self._variants(id, slug, season, episode_id)


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


def test_torznab_empty_query_returns_discovery_feed(tmp_path) -> None:
    db = Database(str(tmp_path / "torznab.db"))
    search_queries: list[str] = []

    def fake_search(query: str) -> list[Title]:
        search_queries.append(query)
        if query == "Dune":
            return [Title(sc_id=1, slug="dune", name="Dune", sc_type="movie", year=2021)]
        return []

    def fake_variants(id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
        assert (id, slug, season, episode_id) == (1, "dune", None, None)
        return [Variant(resolution=1080, bandwidth=2_800_000, url="https://cdn.example/video.m3u8", codecs="avc1.640028")]

    provider = FakeProvider(search=fake_search, variants=fake_variants)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry({"sc": provider})
    try:
        with TestClient(app) as client:
            response = client.get("/torznab/api", params={"t": "search", "apikey": settings.torznab_api_key})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    root = _parse_feed(response.text)
    items = root.findall("./channel/item")
    assert len(items) == 1
    assert items[0].findtext("title") == "Dune.2021.1080p.WEB-DL.H264.ITA-SC"
    assert search_queries == ["Dune"]


def test_torznab_blank_tvsearch_returns_empty_feed() -> None:
    with TestClient(app) as client:
        response = client.get("/torznab/api", params={"t": "tvsearch", "apikey": settings.torznab_api_key})
    assert response.status_code == 200
    root = _parse_feed(response.text)
    assert root.findall("./channel/item") == []


def test_torznab_search_filters_results_and_generates_torrent(tmp_path) -> None:
    db = Database(str(tmp_path / "torznab.db"))

    def fake_search(query: str) -> list[Title]:
        assert query == "query"
        return [
            Title(sc_id=1, slug="dune", name="Dune", sc_type="movie", year=2021),
            Title(sc_id=2, slug="breaking-bad", name="Breaking Bad", sc_type="tv"),
        ]

    def fake_episodes(id: int, slug: str, season: int) -> list[Episode]:
        assert (id, slug, season) == (2, "breaking-bad", 3)
        return [Episode(id=77, number=7, name="One Minute")]

    def fake_variants(id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
        if id == 1:
            assert slug == "dune"
            assert season is None
            assert episode_id is None
        else:
            assert (slug, season, episode_id) == ("breaking-bad", 3, 77)
        return [Variant(resolution=1080, bandwidth=2_800_000, url="https://cdn.example/video.m3u8", codecs="avc1.640028")]

    provider = FakeProvider(search=fake_search, episodes=fake_episodes, variants=fake_variants)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry({"sc": provider})
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


def test_torznab_search_applies_limit_per_provider_not_to_combined_result(tmp_path) -> None:
    # limit/offset are intentionally applied per registered provider rather
    # than to the combined multi-provider result — each source gets its own
    # window instead of one provider's results crowding out another's. With
    # limit=1 and two providers each returning one matching title, the feed
    # should contain releases from both, i.e. more than `limit` releases.
    db = Database(str(tmp_path / "torznab.db"))

    def make_search(name: str) -> Callable[[str], list[Title]]:
        def search(query: str) -> list[Title]:
            return [Title(sc_id=1, slug=name, name=name, sc_type="movie", year=2021)]

        return search

    def make_variants(name: str) -> Callable[[int, str, int | None, int | None], list[Variant]]:
        def variants(id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
            return [Variant(resolution=1080, bandwidth=2_800_000, url=f"https://cdn.example/{name}.m3u8")]

        return variants

    sc_provider = FakeProvider(source="sc", search=make_search("Movie.SC"), variants=make_variants("sc"))
    animeunity_provider = FakeProvider(
        source="animeunity", search=make_search("Movie.AnimeUnity"), variants=make_variants("animeunity")
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry(
        {"sc": sc_provider, "animeunity": animeunity_provider}
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                "/torznab/api", params={"t": "search", "q": "movie", "limit": 1, "apikey": settings.torznab_api_key}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    root = _parse_feed(response.text)
    titles = {item.findtext("title") for item in root.findall("./channel/item")}
    assert titles == {"Movie.SC.2021.1080p.WEB-DL.H264.ITA-SC", "Movie.AnimeUnity.2021.1080p.WEB-DL.H264.ITA-SC"}


def test_torznab_animeunity_movie_resolves_episode_id_for_variants(tmp_path) -> None:
    # AnimeUnity, unlike SC, has no direct-by-id embed endpoint: even a movie
    # is a single-episode entry there, and resolve_variants needs that
    # episode's id or it returns nothing (see resolver.py's `if not
    # episode_id: return []`). Regression test for the bug where AnimeUnity
    # movies were built with episode_id=None (same as SC), silently falling
    # back to a source_url-less placeholder release that fails to download.
    db = Database(str(tmp_path / "torznab.db"))

    def fake_search(query: str) -> list[Title]:
        return [Title(sc_id=9, slug="lupin-iii-fuga-da-alcatraz", name="Lupin III - Fuga da Alcatraz", sc_type="Movie", year=2001)]

    def fake_episodes(id: int, slug: str, season: int) -> list[Episode]:
        assert (id, slug) == (9, "lupin-iii-fuga-da-alcatraz")
        return [Episode(id=555, number=1, name="Lupin III - Fuga da Alcatraz")]

    def fake_variants(id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
        assert episode_id == 555
        return [Variant(resolution=1080, bandwidth=2_800_000, url="https://cdn.example/lupin.m3u8")]

    provider = FakeProvider(source="animeunity", search=fake_search, episodes=fake_episodes, variants=fake_variants)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry({"animeunity": provider})
    try:
        with TestClient(app) as client:
            response = client.get(
                "/torznab/api",
                params={"t": "movie", "q": "Lupin III - Fuga da Alcatraz", "apikey": settings.torznab_api_key},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    root = _parse_feed(response.text)
    items = root.findall("./channel/item")
    assert len(items) == 1
    assert items[0].findtext("title") == "Lupin.III.-.Fuga.da.Alcatraz.2001.1080p.WEB-DL.H264.ITA-SC"


def test_torznab_skips_release_when_no_variant_resolves(tmp_path) -> None:
    # Nothing downstream (run_download_job) ever re-resolves a release's
    # source_url after search time — it rejects an empty one outright — so a
    # title whose variant resolution comes back empty must not produce a
    # release at all. Doing so used to synthesize a placeholder with
    # source_url="" that looked like a normal result but was guaranteed to
    # fail the moment Sonarr/Radarr grabbed it.
    db = Database(str(tmp_path / "torznab.db"))

    def fake_search(query: str) -> list[Title]:
        return [Title(sc_id=1, slug="dune", name="Dune", sc_type="movie", year=2021)]

    provider = FakeProvider(source="sc", search=fake_search)  # default variants() returns []
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry({"sc": provider})
    try:
        with TestClient(app) as client:
            response = client.get(
                "/torznab/api", params={"t": "movie", "q": "dune", "apikey": settings.torznab_api_key}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    root = _parse_feed(response.text)
    assert root.findall("./channel/item") == []
    assert db.list_releases() == []


def test_torznab_anime_only_category_skips_sc_provider(tmp_path) -> None:
    # cat=5070 (Anime, see torznab/caps.py) has no business also querying SC
    # (StreamingCommunity, live-action only) — doing so used to waste an
    # entire search's worth of live variant-resolution retries against SC
    # titles that were never going to be anime, slow enough to time out the
    # caller (e.g. Sonarr's own tvsearch request for an Anime-tagged series).
    db = Database(str(tmp_path / "torznab.db"))

    def sc_search(query: str) -> list[Title]:
        raise AssertionError("SC provider must not be queried for an Anime-only category request")

    def animeunity_search(query: str) -> list[Title]:
        return [Title(sc_id=1, slug="slime", name="Slime", sc_type="TV", source="animeunity", year=2018)]

    sc_provider = FakeProvider(source="sc", search=sc_search)
    animeunity_provider = FakeProvider(source="animeunity", search=animeunity_search)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry(
        {"sc": sc_provider, "animeunity": animeunity_provider}
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                "/torznab/api",
                params={"t": "tvsearch", "q": "slime", "cat": "5070", "apikey": settings.torznab_api_key},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_torznab_mixed_category_still_queries_every_provider(tmp_path) -> None:
    # A request whose category set mixes Anime with a general TV/Movie
    # category (or carries an id this app doesn't recognize) keeps the
    # original "query everything" behavior rather than risking narrowing a
    # broader request the caller actually wanted answered by both sources.
    db = Database(str(tmp_path / "torznab.db"))
    calls: list[str] = []

    def make_search(name: str):
        def search(query: str) -> list[Title]:
            calls.append(name)
            return []

        return search

    sc_provider = FakeProvider(source="sc", search=make_search("sc"))
    animeunity_provider = FakeProvider(source="animeunity", search=make_search("animeunity"))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry(
        {"sc": sc_provider, "animeunity": animeunity_provider}
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                "/torznab/api",
                params={"t": "tvsearch", "q": "slime", "cat": "5000,5070", "apikey": settings.torznab_api_key},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert set(calls) == {"sc", "animeunity"}
