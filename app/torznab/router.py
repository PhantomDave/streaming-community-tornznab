from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.deps import get_db, get_sc_client
from app.db import Database
from app.magnet import MagnetDescriptor, infohash_from_descriptor, torrent_stub_payload
from app.models import Release, Title, Variant, now_utc
from app.sc.client import StreamingCommunityClient
from app.sc.resolver import resolve_variants
from app.sc.search import search_titles
from app.sc.titles import get_season_episodes
from app.torznab.caps import build_caps_xml
from app.torznab.feed import build_feed_xml
from app.torznab.naming import build_release_name

router = APIRouter(prefix="/torznab", tags=["torznab"])


@router.get("/api")
async def torznab_api(
    t: str = Query(...),
    apikey: str | None = Query(default=None),
    q: str | None = Query(default=None),
    cat: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    imdbid: str | None = Query(default=None),
    tmdbid: str | None = Query(default=None),
    tvdbid: str | None = Query(default=None),
    season: int | None = Query(default=None),
    ep: int | None = Query(default=None),
    db: Database = Depends(get_db),
    sc_client: StreamingCommunityClient = Depends(get_sc_client),
) -> Response:
    if t == "caps":
        return Response(content=build_caps_xml(), media_type="application/xml")

    if apikey != settings.torznab_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if t not in {"search", "tvsearch", "movie"}:
        raise HTTPException(status_code=400, detail="Unsupported Torznab function")

    query = _build_query(q=q, imdbid=imdbid, tmdbid=tmdbid, tvdbid=tvdbid)
    if not query:
        return Response(content=build_feed_xml(query="", releases=[]), media_type="application/xml")

    titles = await _safe_search(sc_client, query)
    sliced = titles[offset : offset + limit]
    releases = await _build_releases(
        db,
        sc_client,
        sliced,
        search_type=t,
        season=season,
        episode=ep,
    )
    return Response(content=build_feed_xml(query=query, releases=releases), media_type="application/xml")


@router.get("/dl/{infohash}.torrent")
async def torrent_download(infohash: str, db: Database = Depends(get_db)) -> Response:
    release = db.get_release(infohash.lower())
    if not release:
        raise HTTPException(status_code=404, detail="Unknown release")
    payload = torrent_stub_payload(release.infohash, release.release_name)
    return Response(content=payload, media_type="application/x-bittorrent")


def _build_query(*, q: str | None, imdbid: str | None, tmdbid: str | None, tvdbid: str | None) -> str:
    if q and q.strip():
        return q.strip()
    for candidate in (imdbid, tmdbid, tvdbid):
        if candidate and candidate.strip():
            return candidate.strip()
    return ""


async def _safe_search(sc_client: StreamingCommunityClient, query: str) -> list[Title]:
    try:
        return await search_titles(sc_client, query)
    except Exception:
        return []


async def _build_releases(
    db: Database,
    sc_client: StreamingCommunityClient,
    titles: list[Title],
    *,
    search_type: str,
    season: int | None,
    episode: int | None,
) -> list[Release]:
    releases: list[Release] = []
    for title in titles:
        if search_type == "movie" and title.sc_type.lower() == "tv":
            continue
        if search_type == "tvsearch" and title.sc_type.lower() != "tv":
            continue
        selected_episode_id: int | None = None
        selected_episode_number = episode
        selected_season = season
        if title.sc_type.lower() == "tv" and season is not None and episode is not None:
            episodes = await _safe_episodes(sc_client, title.sc_id, title.slug, season)
            selected = next((ep_item for ep_item in episodes if ep_item.number == episode), None)
            selected_episode_id = selected.id if selected else None
            selected_episode_number = selected.number if selected else episode
        variants = await _safe_variants(
            sc_client,
            sc_id=title.sc_id,
            slug=title.slug,
            season=selected_season,
            episode_id=selected_episode_id,
        )
        selected_variants = variants or _fallback_variants()
        for variant in selected_variants:
            descriptor = MagnetDescriptor(
                sc_id=title.sc_id,
                sc_type=title.sc_type,
                slug=title.slug,
                season=selected_season,
                episode=selected_episode_number,
                resolution=variant.resolution,
                audio=variant.audio,
            )
            infohash = infohash_from_descriptor(descriptor)
            release_name = build_release_name(
                title=title,
                resolution=variant.resolution,
                codecs=variant.codecs,
                audio=variant.audio,
                season=selected_season,
                episode=selected_episode_number,
                release_group=settings.release_group,
            )
            size_estimate = _estimate_size(variant.bandwidth)
            release = Release(
                infohash=infohash,
                sc_id=title.sc_id,
                sc_type=title.sc_type,
                slug=title.slug,
                title=title.name,
                year=title.year,
                season=selected_season,
                episode=selected_episode_number,
                resolution=variant.resolution,
                audio=variant.audio,
                size_estimate=size_estimate,
                release_name=release_name,
                source_url=variant.url,
                created_at=now_utc(),
            )
            db.upsert_release(release)
            releases.append(release)
    return releases


def _fallback_variants() -> list[Variant]:
    return [Variant(resolution=res, bandwidth=None, url="", codecs="avc1", audio="ITA") for res in settings.quality_list]


def _estimate_size(bandwidth: int | None, duration_seconds: int = 5400) -> int:
    if not bandwidth:
        return 2 * 1024 * 1024 * 1024
    return int((bandwidth * duration_seconds) / 8)


async def _safe_episodes(sc_client: StreamingCommunityClient, sc_id: int, slug: str, season: int):
    try:
        return await get_season_episodes(sc_client, sc_id, slug, season)
    except Exception:
        return []


async def _safe_variants(
    sc_client: StreamingCommunityClient,
    *,
    sc_id: int,
    slug: str,
    season: int | None,
    episode_id: int | None,
):
    try:
        return await resolve_variants(sc_client, sc_id=sc_id, slug=slug, season=season, episode_id=episode_id)
    except Exception:
        return []
