"""Shared parsing for vixcloud.co embed pages and HLS master playlists.

Any provider that resolves a title's video to a vixcloud.co embed URL (e.g.
StreamingCommunity, AnimeUnity) can hand the fetched embed HTML to
`resolve_variants_from_embed` to get back `Variant` objects, regardless of how
that provider located the embed URL in the first place.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from app.config import settings
from app.models import Variant

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

# window.streams holds the per-server base playlist URLs, e.g. .../playlist/278687?ub=1
STREAMS_PATTERN = re.compile(r"window\.streams\s*=\s*(\[[^\]]*\]);")
# window.masterPlaylist = { params: {token, expires, asn}, url: '...' } supplies the auth token/expiry.
MASTER_PLAYLIST_MARKER = "window.masterPlaylist"
TOKEN_PATTERN = re.compile(r"'token':\s*'([^']+)'")
EXPIRES_PATTERN = re.compile(r"'expires':\s*'([^']+)'")
ASN_PATTERN = re.compile(r"'asn':\s*'([^']*)'")


class SupportsGetText(Protocol):
    async def get_text(
        self, path: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> str: ...


async def resolve_variants_from_embed(client: SupportsGetText, *, embed_html: str, embed_url: str) -> list[Variant]:
    """Resolve an already-fetched vixcloud embed page into HLS `Variant`s.

    Mirrors the vixcloud player's own JS: the base URL comes from
    `window.streams[active].url` (which carries a `?ub=1`/`?ab=1` server flag),
    the `window.masterPlaylist.params` (token/expires/asn) are appended, and an
    `h=1` flag is added when the embed URL's own `canPlayFHD` query param is set.
    """
    direct_url = extract_m3u8_url(embed_html)
    master_url = direct_url or _build_playlist_url(embed_html, embed_url)
    if not master_url:
        return []
    playlist_text = await client.get_text(master_url, headers={"Referer": embed_url})
    return parse_master_playlist(playlist_text, master_url)


def extract_m3u8_url(text: str) -> str | None:
    match = M3U8_PATTERN.search(text)
    return match.group(0) if match else None


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


def parse_master_playlist(text: str, base_url: str) -> list[Variant]:
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
