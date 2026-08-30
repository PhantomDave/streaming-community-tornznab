from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import asdict
from urllib.parse import urljoin

from app.config import settings
from app.models import Variant
from app.sc.client import StreamingCommunityClient
from app.vixcloud import extract_m3u8_url, resolve_variants_from_embed

logger = logging.getLogger(__name__)

# The iframe endpoint wraps a nested <iframe src="https://vixcloud.co/embed/..."> player.
IFRAME_SRC_PATTERN = re.compile(r'<iframe[^>]+src="([^"]+)"')


async def resolve_variants(
    client: StreamingCommunityClient,
    *,
    sc_id: int,
    slug: str,
    season: int | None,
    episode_id: int | None,
) -> list[Variant]:
    cache_key = f"playlist:{sc_id}:{slug}:{season or 0}:{episode_id or 0}"
    cached = client.get_cached(cache_key, playlist=True)
    if isinstance(cached, list) and cached:
        logger.debug("Playlist cache hit for %s (%d variant(s))", cache_key, len(cached))
        return [Variant(**entry) for entry in cached if isinstance(entry, dict)]

    # Use locale-prefixed iframe endpoint
    iframe_path = f"/{settings.locale}/iframe/{sc_id}"
    request_params = {"episode_id": episode_id} if episode_id else None
    logger.info("Resolving stream for sc_id=%s slug=%s season=%s episode_id=%s", sc_id, slug, season, episode_id)
    iframe_html = await client.get_text(iframe_path, params=request_params)
    iframe_url = urljoin(settings.sc_base_url or "", iframe_path)

    embed_html, embed_url = iframe_html, iframe_url
    if not extract_m3u8_url(iframe_html):
        embed_src_match = IFRAME_SRC_PATTERN.search(iframe_html)
        if not embed_src_match:
            logger.warning("Could not resolve master playlist URL for sc_id=%s slug=%s", sc_id, slug)
            return []
        embed_url = html_lib.unescape(embed_src_match.group(1))
        embed_html = await client.get_text(embed_url, headers={"Referer": iframe_url})

    variants = await resolve_variants_from_embed(client, embed_html=embed_html, embed_url=embed_url)
    logger.info(
        "Resolved %d variant(s) for sc_id=%s slug=%s: %s",
        len(variants),
        sc_id,
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
