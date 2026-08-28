from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import asdict
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from app.config import settings
from app.models import Variant
from app.sc.client import StreamingCommunityClient

logger = logging.getLogger(__name__)

M3U8_PATTERN = re.compile(r"https?://[^\s\"']+\.m3u8[^\s\"']*")
STREAM_INF_PATTERN = re.compile(r"#EXT-X-STREAM-INF:(?P<attrs>[^\n]+)\n(?P<url>[^\n]+)")
RESOLUTION_PATTERN = re.compile(r"RESOLUTION=\d+x(?P<height>\d+)")
BANDWIDTH_PATTERN = re.compile(r"BANDWIDTH=(?P<bandwidth>\d+)")
CODECS_PATTERN = re.compile(r'CODECS="(?P<codecs>[^"]+)"')
AUDIO_GROUP_PATTERN = re.compile(r'AUDIO="(?P<group>[^"]+)"')
ATTR_PATTERN = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')

# vixcloud (and other HLS packagers) commonly serve audio as one or more
# EXT-X-MEDIA renditions in their own GROUP-ID, referenced from
# #EXT-X-STREAM-INF via AUDIO="...", rather than muxed into the video
# segments — the STREAM-INF's own CODECS list still names the audio codec
# even though it lives at a wholly separate URL. A Variant.url pointing only
# at the video rendition therefore has picture but no sound.
MEDIA_TAG_PATTERN = re.compile(r"#EXT-X-MEDIA:(?P<attrs>[^\n]+)")

# The iframe endpoint wraps a nested <iframe src="https://vixcloud.co/embed/..."> player.
IFRAME_SRC_PATTERN = re.compile(r'<iframe[^>]+src="([^"]+)"')
# window.streams holds the per-server base playlist URLs, e.g. .../playlist/278687?ub=1
STREAMS_PATTERN = re.compile(r"window\.streams\s*=\s*(\[[^\]]*\]);")
# window.masterPlaylist = { params: {token, expires, asn}, url: '...' } supplies the auth token/expiry.
MASTER_PLAYLIST_MARKER = "window.masterPlaylist"
TOKEN_PATTERN = re.compile(r"'token':\s*'([^']+)'")
EXPIRES_PATTERN = re.compile(r"'expires':\s*'([^']+)'")
ASN_PATTERN = re.compile(r"'asn':\s*'([^']*)'")


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

    resolved = await _resolve_master_playlist_url(client, iframe_html, iframe_url)
    if not resolved:
        logger.warning("Could not resolve master playlist URL for sc_id=%s slug=%s", sc_id, slug)
        return []
    master_url, playlist_referer = resolved
    logger.debug("Master playlist URL resolved for sc_id=%s: %s", sc_id, master_url)

    playlist_text = await client.get_text(master_url, headers={"Referer": playlist_referer})
    variants = _parse_master_playlist(playlist_text, master_url)
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


async def _resolve_master_playlist_url(
    client: StreamingCommunityClient, iframe_html: str, iframe_url: str
) -> tuple[str, str] | None:
    """Resolve the final .m3u8 master playlist URL from the iframe wrapper page.

    Mirrors the vixcloud player's own JS: the base URL comes from
    `window.streams[active].url` (which carries a `?ub=1`/`?ab=1` server flag),
    the `window.masterPlaylist.params` (token/expires/asn) are appended, and an
    `h=1` flag is added when the embed URL's own `canPlayFHD` query param is set.
    """
    direct_match = _extract_m3u8_url(iframe_html)
    if direct_match:
        return direct_match, iframe_url

    embed_src_match = IFRAME_SRC_PATTERN.search(iframe_html)
    if not embed_src_match:
        return None
    embed_url = html_lib.unescape(embed_src_match.group(1))
    embed_html = await client.get_text(embed_url, headers={"Referer": iframe_url})

    direct_match = _extract_m3u8_url(embed_html)
    if direct_match:
        return direct_match, embed_url

    playlist_url = _build_playlist_url(embed_html, embed_url)
    if not playlist_url:
        return None
    return playlist_url, embed_url


def _build_playlist_url(embed_html: str, embed_url: str) -> str | None:
    base_url = _extract_active_stream_url(embed_html)
    if not base_url:
        return None

    master_playlist_idx = embed_html.find(MASTER_PLAYLIST_MARKER)
    if master_playlist_idx == -1:
        return None
    # The object literal is short; a fixed-size window avoids scanning/backtracking over the whole page.
    raw_object = embed_html[master_playlist_idx : master_playlist_idx + 500]
    token_match = TOKEN_PATTERN.search(raw_object)
    expires_match = EXPIRES_PATTERN.search(raw_object)
    if not (token_match and expires_match):
        return None

    query: dict[str, str] = {"token": token_match.group(1), "expires": expires_match.group(1)}
    asn_match = ASN_PATTERN.search(raw_object)
    if asn_match and asn_match.group(1):
        query["asn"] = asn_match.group(1)
    if _wants_fhd(embed_url):
        query["h"] = "1"

    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(query)}"


def _extract_active_stream_url(embed_html: str) -> str | None:
    streams_match = STREAMS_PATTERN.search(embed_html)
    if not streams_match:
        return None
    try:
        streams = json.loads(streams_match.group(1))
    except ValueError:
        return None
    active_stream = next((s for s in streams if isinstance(s, dict) and s.get("active")), None)
    if active_stream is None and streams and isinstance(streams[0], dict):
        active_stream = streams[0]
    base_url = active_stream.get("url") if active_stream else None
    return base_url if isinstance(base_url, str) else None


def _wants_fhd(embed_url: str) -> bool:
    embed_query = parse_qs(urlparse(embed_url).query)
    return embed_query.get("canPlayFHD", ["0"])[0] not in ("0", "")


def _extract_m3u8_url(text: str) -> str | None:
    match = M3U8_PATTERN.search(text)
    return match.group(0) if match else None


def _parse_master_playlist(text: str, base_url: str) -> list[Variant]:
    audio_groups = _parse_audio_renditions(text, base_url)
    variants: list[Variant] = []
    for match in STREAM_INF_PATTERN.finditer(text):
        attrs = match.group("attrs")
        raw_url = match.group("url").strip()
        resolution = _extract_resolution(attrs)
        if not resolution:
            continue
        bandwidth = _extract_bandwidth(attrs)
        codecs = _extract_codecs(attrs)
        audio_group_match = AUDIO_GROUP_PATTERN.search(attrs)
        audio_url, audio_label = _select_audio_rendition(
            audio_groups.get(audio_group_match.group("group"), []) if audio_group_match else []
        )
        variants.append(
            Variant(
                resolution=resolution,
                bandwidth=bandwidth,
                codecs=codecs,
                url=urljoin(base_url, raw_url),
                audio=audio_label or "ITA",
                audio_url=audio_url,
            )
        )
    variants.sort(key=lambda variant: variant.resolution, reverse=True)
    return variants


def _parse_attributes(attrs: str) -> dict[str, str]:
    return {key: value.strip('"') for key, value in ATTR_PATTERN.findall(attrs)}


def _parse_audio_renditions(text: str, base_url: str) -> dict[str, list[tuple[str, str, bool]]]:
    """Group EXT-X-MEDIA audio renditions by GROUP-ID.

    Each entry is (language, absolute URI, is_default).
    """
    groups: dict[str, list[tuple[str, str, bool]]] = {}
    for match in MEDIA_TAG_PATTERN.finditer(text):
        attrs = _parse_attributes(match.group("attrs"))
        if attrs.get("TYPE") != "AUDIO":
            continue
        group_id = attrs.get("GROUP-ID")
        uri = attrs.get("URI")
        if not group_id or not uri:
            continue
        language = attrs.get("LANGUAGE", "").lower()
        is_default = attrs.get("DEFAULT", "").upper() == "YES"
        groups.setdefault(group_id, []).append((language, urljoin(base_url, uri), is_default))
    return groups


def _select_audio_rendition(renditions: list[tuple[str, str, bool]]) -> tuple[str, str]:
    """Pick one audio rendition per settings.preferred_audio_list, falling back
    to the DEFAULT=YES entry and then the first one listed."""
    if not renditions:
        return "", ""
    for preferred in settings.preferred_audio_list:
        for language, uri, _ in renditions:
            if language == preferred:
                return uri, language.upper()
    for language, uri, is_default in renditions:
        if is_default:
            return uri, language.upper()
    language, uri, _ = renditions[0]
    return uri, language.upper()


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
