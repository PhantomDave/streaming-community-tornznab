from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
import xml.etree.ElementTree as ET

from app.config import settings
from app.magnet import build_magnet, make_public_download_url
from app.models import Release
from app.torznab.categories import category_for_release

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
ET.register_namespace("torznab", TORZNAB_NS)


def build_feed_xml(*, query: str, releases: list[Release]) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "sctorznab"
    ET.SubElement(channel, "description").text = "StreamingCommunity bridge"
    ET.SubElement(channel, "link").text = settings.public_url

    for release in releases:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = release.release_name
        ET.SubElement(item, "guid").text = release.infohash
        ET.SubElement(item, "link").text = make_public_download_url(settings.public_url, release.infohash)
        magnet = build_magnet(release.infohash, release.release_name)
        ET.SubElement(item, "enclosure", {"url": magnet, "type": "application/x-bittorrent"})
        ET.SubElement(item, "size").text = str(release.size_estimate)
        ET.SubElement(item, "pubDate").text = _format_date(release.created_at)
        ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": "category", "value": str(category_for_release(release.sc_type, release.resolution))})
        ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": "seeders", "value": "100"})
        ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": "peers", "value": "100"})
        ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": "magneturl", "value": magnet})
        ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": "infohash", "value": release.infohash})
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")


def _format_date(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)
