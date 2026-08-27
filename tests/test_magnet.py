from app.magnet import MagnetDescriptor, build_magnet, extract_infohash_from_magnet, infohash_from_descriptor


def test_infohash_is_deterministic() -> None:
    descriptor = MagnetDescriptor(
        sc_id=123,
        sc_type="movie",
        slug="dune",
        season=None,
        episode=None,
        resolution=1080,
        audio="ITA",
    )
    assert infohash_from_descriptor(descriptor) == infohash_from_descriptor(descriptor)


def test_extract_infohash_from_magnet() -> None:
    magnet = build_magnet("abc123", "Release.Name")
    assert extract_infohash_from_magnet(magnet) == "abc123"
