from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.deps import get_db, get_provider_registry
from app.db import Database
from app.magnet import MagnetDescriptor, infohash_from_descriptor, torrent_stub_payload
from app.models import Episode, Release, Title, Variant, now_utc
from app.provider import Provider, ProviderRegistry
from app.torznab.caps import build_caps_xml
from app.torznab.feed import build_feed_xml
from app.torznab.naming import build_release_name

router = APIRouter(prefix="/torznab", tags=["torznab"])
_DISCOVERY_TERMS = ("Dune", "Inception", "Breaking Bad", "Matrix")
logger = logging.getLogger(__name__)


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
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> Response:
    logger.info("Torznab request t=%s q=%r cat=%s imdbid=%s tmdbid=%s tvdbid=%s season=%s ep=%s", t, q, cat, imdbid, tmdbid, tvdbid, season, ep)

    if t == "caps":
        return Response(content=build_caps_xml(), media_type="application/xml")

    if apikey != settings.torznab_api_key:
        logger.warning("Torznab request rejected: invalid API key")
        raise HTTPException(status_code=401, detail="Invalid API key")

    if t not in {"search", "tvsearch", "movie"}:
        logger.warning("Torznab request rejected: unsupported function t=%s", t)
        raise HTTPException(status_code=400, detail="Unsupported Torznab function")

    query = _build_query(q=q, imdbid=imdbid, tmdbid=tmdbid, tvdbid=tvdbid)
    if not query:
        if t != "search":
            return Response(content=build_feed_xml(query="", releases=[]), media_type="application/xml")
        cached_releases = db.list_releases(limit=limit, offset=offset)
        if cached_releases:
            logger.info("Torznab empty-query search served %d cached release(s)", len(cached_releases))
            return Response(content=build_feed_xml(query="", releases=cached_releases), media_type="application/xml")
        releases: list[Release] = []
        title_count = 0
        for provider in registry.all():
            titles = await _discover_titles(provider)
            title_count += len(titles)
            releases.extend(
                await _build_releases(
                    db,
                    provider,
                    titles[: limit + offset],
                    search_type=t,
                    season=season,
                    episode=ep,
                )
            )
        logger.info("Torznab discovery search built %d release(s) from %d title(s)", len(releases), title_count)
        return Response(content=build_feed_xml(query="", releases=releases[offset : offset + limit]), media_type="application/xml")

    releases = []
    title_count = 0
    for provider in registry.all():
        titles = await _safe_search(provider, query)
        # limit/offset apply per provider, not to the combined result — each
        # registered source gets its own window rather than one provider's
        # results crowding out another's when multiple are configured. This
        # means the final release count can exceed `limit` (once per provider,
        # further multiplied by each title's resolution variants), which is
        # accepted here the same way a single provider's variant fan-out
        # already made `limit` a bound on titles fetched, not releases returned.
        sliced = titles[offset : offset + limit]
        title_count += len(sliced)
        releases.extend(
            await _build_releases(
                db,
                provider,
                sliced,
                search_type=t,
                season=season,
                episode=ep,
            )
        )
    logger.info("Torznab search t=%s q=%r built %d release(s) from %d title(s)", t, query, len(releases), title_count)
    return Response(content=build_feed_xml(query=query, releases=releases), media_type="application/xml")


@router.get("/dl/{infohash}.torrent")
async def torrent_download(infohash: str, db: Database = Depends(get_db)) -> Response:
    release = db.get_release(infohash.lower())
    if not release:
        logger.warning("Torrent stub request for unknown infohash=%s", infohash)
        raise HTTPException(status_code=404, detail="Unknown release")
    logger.info("Serving torrent stub for %s (%s)", infohash, release.release_name)
    payload = torrent_stub_payload(release.infohash, release.release_name)
    return Response(content=payload, media_type="application/x-bittorrent")


def _build_query(*, q: str | None, imdbid: str | None, tmdbid: str | None, tvdbid: str | None) -> str:
    if q and q.strip():
        return q.strip()
    for candidate in (imdbid, tmdbid, tvdbid):
        if candidate and candidate.strip():
            return candidate.strip()
    return ""


async def _safe_search(provider: Provider, query: str) -> list[Title]:
    try:
        return await provider.search_titles(query)
    except Exception:
        return []


async def _discover_titles(provider: Provider) -> list[Title]:
    for term in _DISCOVERY_TERMS:
        titles = await _safe_search(provider, term)
        if titles:
            return titles
    return []


async def _build_releases(
    db: Database,
    provider: Provider,
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
            episodes = await _safe_episodes(provider, title.sc_id, title.slug, season)
            selected = next((ep_item for ep_item in episodes if ep_item.number == episode), None)
            selected_episode_id = selected.id if selected else None
            selected_episode_number = selected.number if selected else episode
        variants = await _safe_variants(
            provider,
            id=title.sc_id,
            slug=title.slug,
            season=selected_season,
            episode_id=selected_episode_id,
        )
        selected_variants = variants or _fallback_variants()
        for variant in selected_variants:
            descriptor = MagnetDescriptor(
                source=provider.source,
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
                codecs=variant.codecs,
                audio_url=variant.audio_url,
                source=provider.source,
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


async def _safe_episodes(provider: Provider, id: int, slug: str, season: int) -> list[Episode]:
    try:
        return await provider.get_season_episodes(id, slug, season)
    except Exception:
        return []


async def _safe_variants(
    provider: Provider,
    *,
    id: int,
    slug: str,
    season: int | None,
    episode_id: int | None,
) -> list[Variant]:
    try:
        return await provider.resolve_variants(id, slug, season, episode_id)
    except Exception:
        return []
