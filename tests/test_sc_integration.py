from __future__ import annotations

import asyncio
import os
import socket
from urllib.parse import urlparse

import pytest

from app.config import Settings
from app.db import Database
from app.sc.client import StreamingCommunityClient
from app.sc.resolver import resolve_variants
from app.sc.search import search_titles
from app.sc.titles import get_season_episodes

DEFAULT_BASE_URL = "https://streamingcommunityz.studio"


def _require_integration() -> str:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_INTEGRATION_TESTS=1 to run live integration tests")
    base_url = os.getenv("SC_INTEGRATION_BASE_URL", DEFAULT_BASE_URL)
    host = urlparse(base_url).hostname
    if not host:
        pytest.skip("integration base URL is invalid")
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        pytest.skip(f"cannot resolve {host} from this environment")
    return base_url


def _make_client(tmp_path, base_url: str) -> StreamingCommunityClient:
    settings = Settings(
        SC_BASE_URL=base_url,
        DB_PATH=str(tmp_path / "integration.db"),
        REQUEST_TIMEOUT=20,
        MAX_RETRIES=0,
    )
    return StreamingCommunityClient(settings, Database(settings.db_path))


@pytest.mark.integration
def test_live_search_returns_titles(tmp_path) -> None:
    base_url = _require_integration()

    async def run() -> list:
        client = _make_client(tmp_path, base_url)
        try:
            return await search_titles(client, "dune")
        finally:
            await client.close()

    titles = asyncio.run(run())
    assert titles
    assert isinstance(titles[0].sc_id, int)
    assert isinstance(titles[0].slug, str)
    assert isinstance(titles[0].name, str)


@pytest.mark.integration
def test_live_tv_title_exposes_episode_listing(tmp_path) -> None:
    base_url = _require_integration()

    async def run():
        client = _make_client(tmp_path, base_url)
        try:
            titles = await search_titles(client, "breaking bad")
            tv_title = next(title for title in titles if title.sc_type.lower() == "tv")
            episodes = await get_season_episodes(client, tv_title.sc_id, tv_title.slug, 1)
            return tv_title, episodes
        finally:
            await client.close()

    tv_title, episodes = asyncio.run(run())
    assert tv_title.name
    assert episodes
    assert isinstance(episodes[0].id, int)
    assert isinstance(episodes[0].number, int)
    assert isinstance(episodes[0].name, str)


@pytest.mark.integration
def test_live_resolve_variants_returns_playable_stream(tmp_path) -> None:
    base_url = _require_integration()

    async def run():
        client = _make_client(tmp_path, base_url)
        try:
            titles = await search_titles(client, "breaking bad")
            tv_title = next(title for title in titles if title.sc_type.lower() == "tv")
            episodes = await get_season_episodes(client, tv_title.sc_id, tv_title.slug, 1)
            episode = episodes[0]
            return await resolve_variants(
                client,
                sc_id=tv_title.sc_id,
                slug=tv_title.slug,
                season=1,
                episode_id=episode.id,
            )
        finally:
            await client.close()

    variants = asyncio.run(run())
    assert variants
    assert all(variant.url.startswith("http") for variant in variants)
    assert all(variant.resolution > 0 for variant in variants)


@pytest.mark.integration
def test_live_movie_search_and_stream_resolution_inception(tmp_path) -> None:
    base_url = _require_integration()

    async def run():
        client = _make_client(tmp_path, base_url)
        try:
            titles = await search_titles(client, "inception")
            movie = next(title for title in titles if title.sc_type.lower() == "movie")
            variants = await resolve_variants(
                client, sc_id=movie.sc_id, slug=movie.slug, season=None, episode_id=None
            )
            return movie, variants
        finally:
            await client.close()

    movie, variants = asyncio.run(run())
    assert movie.name
    assert variants
    assert all(variant.url.startswith("http") for variant in variants)


@pytest.mark.integration
def test_live_movie_search_and_stream_resolution_matrix(tmp_path) -> None:
    base_url = _require_integration()

    async def run():
        client = _make_client(tmp_path, base_url)
        try:
            titles = await search_titles(client, "the matrix")
            movie = next(title for title in titles if title.sc_type.lower() == "movie")
            variants = await resolve_variants(
                client, sc_id=movie.sc_id, slug=movie.slug, season=None, episode_id=None
            )
            return movie, variants
        finally:
            await client.close()

    movie, variants = asyncio.run(run())
    assert movie.name
    assert variants
    assert all(variant.resolution > 0 for variant in variants)


@pytest.mark.integration
def test_live_movie_search_and_stream_resolution_john_wick(tmp_path) -> None:
    base_url = _require_integration()

    async def run():
        client = _make_client(tmp_path, base_url)
        try:
            titles = await search_titles(client, "john wick")
            movie = next(title for title in titles if title.sc_type.lower() == "movie")
            variants = await resolve_variants(
                client, sc_id=movie.sc_id, slug=movie.slug, season=None, episode_id=None
            )
            return movie, variants
        finally:
            await client.close()

    movie, variants = asyncio.run(run())
    assert movie.name
    assert variants
    assert all(variant.url.startswith("http") for variant in variants)


@pytest.mark.integration
def test_live_tv_search_and_stream_resolution_stranger_things(tmp_path) -> None:
    base_url = _require_integration()

    async def run():
        client = _make_client(tmp_path, base_url)
        try:
            titles = await search_titles(client, "stranger things")
            tv_title = next(title for title in titles if title.sc_type.lower() == "tv")
            episodes = await get_season_episodes(client, tv_title.sc_id, tv_title.slug, 1)
            episode = episodes[0]
            variants = await resolve_variants(
                client,
                sc_id=tv_title.sc_id,
                slug=tv_title.slug,
                season=1,
                episode_id=episode.id,
            )
            return tv_title, episodes, variants
        finally:
            await client.close()

    tv_title, episodes, variants = asyncio.run(run())
    assert tv_title.name
    assert episodes
    assert variants
    assert all(variant.url.startswith("http") for variant in variants)


@pytest.mark.integration
def test_live_tv_search_and_stream_resolution_friends(tmp_path) -> None:
    base_url = _require_integration()

    async def run():
        client = _make_client(tmp_path, base_url)
        try:
            titles = await search_titles(client, "friends")
            tv_title = next(title for title in titles if title.sc_type.lower() == "tv")
            episodes = await get_season_episodes(client, tv_title.sc_id, tv_title.slug, 1)
            episode = episodes[0]
            variants = await resolve_variants(
                client,
                sc_id=tv_title.sc_id,
                slug=tv_title.slug,
                season=1,
                episode_id=episode.id,
            )
            return tv_title, episodes, variants
        finally:
            await client.close()

    tv_title, episodes, variants = asyncio.run(run())
    assert tv_title.name
    assert episodes
    assert variants
    assert all(variant.resolution > 0 for variant in variants)
