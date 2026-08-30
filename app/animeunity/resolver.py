from __future__ import annotations

import logging
from dataclasses import asdict

from app.animeunity.client import AnimeUnityClient
from app.config import settings
from app.models import Variant
from app.vixcloud import resolve_variants_from_embed

logger = logging.getLogger(__name__)


async def resolve_variants(
    client: AnimeUnityClient,
    *,
    id: int,
    slug: str,
    season: int | None,
    episode_id: int | None,
) -> list[Variant]:
    cache_key = f"animeunity:playlist:{id}:{slug}:{season or 0}:{episode_id or 0}"
    cached = client.get_cached(cache_key, playlist=True)
    if isinstance(cached, list) and cached:
        logger.debug("Playlist cache hit for %s (%d variant(s))", cache_key, len(cached))
        return [Variant(**entry) for entry in cached if isinstance(entry, dict)]
    if not episode_id:
        return []

    logger.info("Resolving stream for animeunity id=%s slug=%s episode_id=%s", id, slug, episode_id)
    embed_url = (await client.get_text(f"/embed-url/{episode_id}")).strip()
    embed_html = await client.get_text(embed_url)

    variants = await resolve_variants_from_embed(client, embed_html=embed_html, embed_url=embed_url)
    logger.info(
        "Resolved %d variant(s) for animeunity id=%s slug=%s: %s",
        len(variants),
        id,
        slug,
        [v.resolution for v in variants],
    )
    client.set_cached(
        cache_key,
        [asdict(variant) for variant in variants],
        ttl=settings.playlist_cache_ttl,
        playlist=True,
    )
    return variants
