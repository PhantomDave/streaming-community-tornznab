from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urlparse


@dataclass(slots=True)
class MagnetDescriptor:
    sc_id: int
    sc_type: str
    slug: str
    season: int | None
    episode: int | None
    resolution: int
    audio: str

    def canonical(self) -> str:
        return f"{self.sc_id}:{self.sc_type}:{self.slug}:{self.season or 0}:{self.episode or 0}:{self.resolution}:{self.audio.lower()}"


def infohash_from_descriptor(descriptor: MagnetDescriptor) -> str:
    return hashlib.sha1(descriptor.canonical().encode("utf-8")).hexdigest()


def build_magnet(infohash: str, display_name: str) -> str:
    return f"magnet:?xt=urn:btih:{infohash}&dn={quote(display_name)}"


def extract_infohash_from_magnet(magnet: str) -> str | None:
    parsed = urlparse(magnet)
    if parsed.scheme != "magnet":
        return None
    params = parse_qs(parsed.query)
    xt_values = params.get("xt", [])
    for xt in xt_values:
        if xt.startswith("urn:btih:"):
            return xt.split(":")[-1].lower()
    return None


def make_public_download_url(public_url: str, infohash: str) -> str:
    normalized = public_url.rstrip("/")
    return f"{normalized}/dl/{infohash}.torrent"


def torrent_stub_payload(infohash: str, release_name: str) -> bytes:
    content = urlencode({"name": release_name, "hash": infohash})
    return content.encode("utf-8")
