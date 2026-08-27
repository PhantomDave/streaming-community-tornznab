import xml.etree.ElementTree as ET

from app.models import Release, now_utc
from app.torznab.categories import category_for_release
from app.torznab.feed import TORZNAB_NS, build_feed_xml


def test_category_mapping() -> None:
    assert category_for_release("movie", 480) == 2030
    assert category_for_release("movie", 1080) == 2040
    assert category_for_release("movie", 2160) == 2045
    assert category_for_release("tv", 480) == 5030
    assert category_for_release("tv", 2160) == 5045


def test_feed_contains_torznab_attrs() -> None:
    release = Release(
        infohash="abc123",
        sc_id=10,
        sc_type="movie",
        slug="dune",
        title="Dune",
        year=2021,
        season=None,
        episode=None,
        resolution=1080,
        audio="ITA",
        size_estimate=999,
        release_name="Dune.2021.1080p.WEB-DL.H264.ITA-SC",
        source_url="https://example.test/master.m3u8",
        created_at=now_utc(),
    )
    xml_payload = build_feed_xml(query="dune", releases=[release])
    root = ET.fromstring(xml_payload.split("\n", 1)[1])
    items = root.findall("./channel/item")
    assert len(items) == 1
    item = items[0]
    assert item.findtext("guid") == "abc123"
    attrs = {
        attr.attrib["name"]: attr.attrib["value"]
        for attr in item.findall(f"{{{TORZNAB_NS}}}attr")
    }
    assert attrs["category"] == "2040"
    assert attrs["seeders"] == "100"
    assert attrs["infohash"] == "abc123"
