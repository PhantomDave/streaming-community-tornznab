import asyncio
import os
import tempfile

import httpx

from app.animeunity.client import AnimeUnityClient
from app.config import Settings
from app.db import Database

_CSRF_HTML = '<html><head><meta name="csrf-token" content="test-token"></head></html>'


def _make_client(handler) -> AnimeUnityClient:
    settings = Settings(animeunity_base_url="https://www.animeunity.so")
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path)
    client = AnimeUnityClient(settings, db)
    # Swap in a MockTransport so requests never hit the network, while still
    # exercising the real _request/_is_same_origin header-scoping logic.
    client._client = httpx.AsyncClient(
        base_url=settings.animeunity_base_url,
        transport=httpx.MockTransport(handler),
    )
    return client


def test_same_origin_requests_get_animeunity_api_headers() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=_CSRF_HTML)
        seen_headers.append(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler)
    result = asyncio.run(client.get_json("/info_api/1/naruto", params={"start_range": 1, "end_range": 1}))
    assert result == {"ok": True}
    assert len(seen_headers) == 1
    headers = seen_headers[0]
    assert headers["X-CSRF-TOKEN"] == "test-token"
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert headers["Accept"] == "application/json"


def test_cross_origin_requests_do_not_get_animeunity_api_headers() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/" and request.url.host == "www.animeunity.so":
            return httpx.Response(200, text=_CSRF_HTML)
        seen_headers.append(request.headers)
        return httpx.Response(200, text="#EXTM3U\n")

    client = _make_client(handler)
    # A cross-domain vixcloud/CDN fetch, as resolve_variants_from_embed makes.
    result = asyncio.run(client.get_text("https://vixcloud.co/embed/223701", headers={"Referer": "https://www.animeunity.so/"}))
    assert result == "#EXTM3U\n"
    assert len(seen_headers) == 1
    headers = seen_headers[0]
    assert "X-CSRF-TOKEN" not in headers
    assert "X-Requested-With" not in headers
    assert headers.get("Accept") != "application/json"
    # Caller-supplied headers must still pass through untouched.
    assert headers["Referer"] == "https://www.animeunity.so/"


def test_cross_origin_request_does_not_trigger_csrf_priming() -> None:
    csrf_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal csrf_requests
        if request.url.path == "/" and request.url.host == "www.animeunity.so":
            csrf_requests += 1
            return httpx.Response(200, text=_CSRF_HTML)
        return httpx.Response(200, text="#EXTM3U\n")

    client = _make_client(handler)
    asyncio.run(client.get_text("https://vixcloud.co/embed/223701"))
    assert csrf_requests == 0
