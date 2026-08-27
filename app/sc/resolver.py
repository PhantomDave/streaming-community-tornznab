from __future__ import annotations

import re
from urllib.parse import urljoin

from app.config import settings
from app.models import Variant
from app.sc.client import StreamingCommunityClient

M3U8_PATTERN = re.compile(r"https?://[^\s\"']+\.m3u8[^\s\"']*")
STREAM_INF_PATTERN = re.compile(r"#EXT-X-STREAM-INF:(?P<attrs>[^\n]+)\n(?P<url>[^\n]+)")
RESOLUTION_PATTERN = re.compile(r"RESOLUTION=\d+x(?P<height>\d+)")
BANDWIDTH_PATTERN = re.compile(r"BANDWIDTH=(?P<bandwidth>\d+)")
CODECS_PATTERN = re.compile(r'CODECS="(?P<codecs>[^"]+)"')


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
        return [Variant(**entry) for entry in cached if isinstance(entry, dict)]

    iframe_path = f"/iframe/{sc_id}"
    params = {"episode_id": episode_id} if episode_id else None
    iframe_html = await client.get_text(iframe_path, params=params)
    master_url = _extract_m3u8_url(iframe_html)
    if not master_url:
        return []

    playlist_text = await client.get_text(master_url)
    variants = _parse_master_playlist(playlist_text, master_url)
    client.set_cached(
        cache_key,
        [variant.__dict__ for variant in variants],
        ttl=settings.playlist_cache_ttl,
        playlist=True,
    )
    return variants


def _extract_m3u8_url(text: str) -> str | None:
    match = M3U8_PATTERN.search(text)
    return match.group(0) if match else None


def _parse_master_playlist(text: str, base_url: str) -> list[Variant]:
    variants: list[Variant] = []
    for match in STREAM_INF_PATTERN.finditer(text):
        attrs = match.group("attrs")
        raw_url = match.group("url").strip()
        resolution = _extract_resolution(attrs)
        if not resolution:
            continue
        bandwidth = _extract_bandwidth(attrs)
        codecs = _extract_codecs(attrs)
        variants.append(
            Variant(
                resolution=resolution,
                bandwidth=bandwidth,
                codecs=codecs,
                url=urljoin(base_url, raw_url),
            )
        )
    variants.sort(key=lambda variant: variant.resolution, reverse=True)
    return variants


def _extract_resolution(attrs: str) -> int | None:
    match = RESOLUTION_PATTERN.search(attrs)
    if not match:
        return None
    return int(match.group("height"))


def _extract_bandwidth(attrs: str) -> int | None:
    match = BANDWIDTH_PATTERN.search(attrs)
    if not match:
        return None
    return int(match.group("bandwidth"))


def _extract_codecs(attrs: str) -> str:
    match = CODECS_PATTERN.search(attrs)
    if not match:
        return ""
    return match.group("codecs")
