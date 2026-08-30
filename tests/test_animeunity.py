import asyncio
from dataclasses import asdict

from app.animeunity.provider import AnimeUnityProvider
from app.animeunity.resolver import resolve_variants
from app.animeunity.search import search_titles
from app.animeunity.titles import get_season_episodes, get_title_details
from app.models import Variant


class FakeAnimeUnityClient:
    """Duck-typed stand-in for AnimeUnityClient — the animeunity.* modules only
    call get_json/get_text/post_json/get_cached/set_cached, never touch HTTP
    directly, so tests don't need a real client or network access."""

    def __init__(self, *, json_responses: dict | None = None, text_responses: dict | None = None) -> None:
        self._json_responses = json_responses or {}
        self._text_responses = text_responses or {}
        self._cache: dict[str, object] = {}
        self.post_calls: list[tuple[str, dict]] = []
        self.get_json_calls: list[tuple[str, dict]] = []

    async def get_json(self, path, *, params=None, headers=None):
        self.get_json_calls.append((path, dict(params or {})))
        response = self._json_responses[path]
        return response(params) if callable(response) else response

    async def get_text(self, path, *, params=None, headers=None):
        return self._text_responses[path]

    async def post_json(self, path, *, json_body=None, headers=None):
        self.post_calls.append((path, json_body))
        return self._json_responses[path]

    def get_cached(self, cache_key, playlist=False):
        return self._cache.get(cache_key)

    def set_cached(self, cache_key, payload, *, ttl, playlist=False):
        self._cache[cache_key] = payload


def test_search_titles_empty_query_short_circuits() -> None:
    client = FakeAnimeUnityClient()
    titles = asyncio.run(search_titles(client, "   "))
    assert titles == []
    assert client.post_calls == []


def test_search_titles_parses_records() -> None:
    client = FakeAnimeUnityClient(
        json_responses={
            "/livesearch": {
                "records": [
                    {"id": 1469, "slug": "naruto", "title": None, "title_eng": "Naruto", "type": "TV", "date": "2002"},
                    {"missing": "fields"},
                ]
            }
        }
    )
    titles = asyncio.run(search_titles(client, "naruto"))
    assert client.post_calls == [("/livesearch", {"title": "naruto"})]
    assert len(titles) == 1
    title = titles[0]
    assert title.sc_id == 1469
    assert title.slug == "naruto"
    assert title.name == "Naruto"
    assert title.sc_type == "TV"
    assert title.source == "animeunity"
    assert title.year == 2002


def test_get_title_details_caches_payload() -> None:
    client = FakeAnimeUnityClient(json_responses={"/info_api/1469/naruto": {"episodes_count": 220}})
    payload = asyncio.run(get_title_details(client, 1469, "naruto"))
    assert payload == {"episodes_count": 220}
    assert client.get_cached("title:1469:naruto") == {"episodes_count": 220}


def test_get_season_episodes_fetches_full_flat_range_and_casts_string_numbers() -> None:
    # AnimeUnity has no season concept server-side; get_season_episodes probes
    # episodes_count then fetches the whole flat range regardless of `season`.
    client = FakeAnimeUnityClient(
        json_responses={
            "/info_api/1469/naruto": {
                "episodes_count": 2,
                "episodes": [
                    {"id": 28546, "anime_id": 1469, "number": "1", "title": "Arriva Naruto"},
                    {"id": 80045, "anime_id": 1469, "number": "2", "title": "Konohamaru"},
                ],
            }
        }
    )
    episodes = asyncio.run(get_season_episodes(client, 1469, "naruto", season=1))
    assert [e.number for e in episodes] == [1, 2]
    assert episodes[0].id == 28546
    assert episodes[0].name == "Arriva Naruto"


def test_get_season_episodes_paginates_beyond_120_episode_window() -> None:
    # Verified live: AnimeUnity's /info_api rejects any single request whose
    # start_range/end_range window exceeds 120 episodes (a window of 121+
    # silently returns no episodes_count/episodes). Long-running shows (e.g.
    # Naruto's 220 episodes) need multiple chunked requests.
    total = 220

    def make_episode(number: int) -> dict:
        return {"id": 1000 + number, "number": str(number), "title": f"Episode {number}"}

    def fake_response(params: dict) -> dict:
        start, end = params["start_range"], params["end_range"]
        assert end - start + 1 <= 120, "must never request a window larger than 120"
        return {"episodes_count": total, "episodes": [make_episode(n) for n in range(start, end + 1)]}

    client = FakeAnimeUnityClient(json_responses={"/info_api/1469/naruto": fake_response})
    episodes = asyncio.run(get_season_episodes(client, 1469, "naruto", season=1))
    assert [e.number for e in episodes] == list(range(1, total + 1))
    # One probe call (start_range=1,end_range=1) plus two 120-episode windows.
    assert len(client.get_json_calls) == 3


def test_get_season_episodes_returns_empty_when_no_episodes() -> None:
    client = FakeAnimeUnityClient(json_responses={"/info_api/1/x": {"episodes_count": 0}})
    episodes = asyncio.run(get_season_episodes(client, 1, "x", season=1))
    assert episodes == []


def test_resolve_variants_returns_empty_without_episode_id() -> None:
    client = FakeAnimeUnityClient()
    variants = asyncio.run(resolve_variants(client, id=1469, slug="naruto", season=None, episode_id=None))
    assert variants == []


def test_resolve_variants_uses_cache(monkeypatch) -> None:
    client = FakeAnimeUnityClient()
    cached_variant = Variant(resolution=1080, bandwidth=1000, url="https://cdn.example/v.m3u8")
    client.set_cached("playlist:1469:naruto:0:28546", [asdict(cached_variant)], ttl=1, playlist=True)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("should not fetch when cache hit")

    monkeypatch.setattr("app.animeunity.resolver.resolve_variants_from_embed", fail_if_called)
    variants = asyncio.run(resolve_variants(client, id=1469, slug="naruto", season=None, episode_id=28546))
    assert variants == [cached_variant]


def test_resolve_variants_fetches_embed_and_delegates_to_vixcloud(monkeypatch) -> None:
    client = FakeAnimeUnityClient(
        text_responses={
            "/embed-url/28546": "https://vixcloud.co/embed/223701?token=abc&expires=123&canPlayFHD=1\n",
            "https://vixcloud.co/embed/223701?token=abc&expires=123&canPlayFHD=1": "<html>embed</html>",
        }
    )
    expected = [Variant(resolution=1080, bandwidth=2000, url="https://vixcloud.co/playlist.m3u8")]

    async def fake_resolve_from_embed(passed_client, *, embed_html, embed_url):
        assert passed_client is client
        assert embed_html == "<html>embed</html>"
        assert embed_url == "https://vixcloud.co/embed/223701?token=abc&expires=123&canPlayFHD=1"
        return expected

    monkeypatch.setattr("app.animeunity.resolver.resolve_variants_from_embed", fake_resolve_from_embed)
    variants = asyncio.run(resolve_variants(client, id=1469, slug="naruto", season=None, episode_id=28546))
    assert variants == expected
    assert client.get_cached("playlist:1469:naruto:0:28546", playlist=True) is not None


def test_animeunity_provider_delegates_to_module_functions() -> None:
    client = FakeAnimeUnityClient(
        json_responses={
            "/livesearch": {"records": [{"id": 1, "slug": "s", "title_eng": "T", "type": "TV"}]},
        }
    )
    provider = AnimeUnityProvider(client)
    assert provider.source == "animeunity"
    titles = asyncio.run(provider.search_titles("naruto"))
    assert len(titles) == 1
    assert titles[0].source == "animeunity"
