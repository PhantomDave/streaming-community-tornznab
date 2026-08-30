from app.magnet import MagnetDescriptor, build_magnet, extract_infohash_from_magnet, infohash_from_descriptor


def test_infohash_is_deterministic() -> None:
    descriptor = MagnetDescriptor(
        source="sc",
        sc_id=123,
        sc_type="movie",
        slug="dune",
        season=None,
        episode=None,
        resolution=1080,
        audio="ITA",
    )
    assert infohash_from_descriptor(descriptor) == infohash_from_descriptor(descriptor)


def test_infohash_differs_across_sources() -> None:
    base = dict(sc_id=123, sc_type="tv", slug="naruto", season=1, episode=1, resolution=1080, audio="ITA")
    sc_descriptor = MagnetDescriptor(source="sc", **base)
    animeunity_descriptor = MagnetDescriptor(source="animeunity", **base)
    assert infohash_from_descriptor(sc_descriptor) != infohash_from_descriptor(animeunity_descriptor)


def test_extract_infohash_from_magnet() -> None:
    magnet = build_magnet("abc123", "Release.Name")
    assert extract_infohash_from_magnet(magnet) == "abc123"
