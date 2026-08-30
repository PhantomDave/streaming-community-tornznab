from __future__ import annotations

import xml.etree.ElementTree as ET


def build_caps_xml() -> str:
    caps = ET.Element("caps")
    ET.SubElement(caps, "server", {"version": "1.1", "title": "sctorznab", "strapline": "StreamingCommunity bridge"})
    ET.SubElement(caps, "limits", {"max": "100", "default": "50"})

    searching = ET.SubElement(caps, "searching")
    ET.SubElement(searching, "search", {"available": "yes", "supportedParams": "q"})
    ET.SubElement(searching, "tv-search", {"available": "yes", "supportedParams": "q,tvdbid,imdbid,season,ep"})
    ET.SubElement(searching, "movie-search", {"available": "yes", "supportedParams": "q,imdbid,tmdbid"})

    categories = ET.SubElement(caps, "categories")
    movies = ET.SubElement(categories, "category", {"id": "2000", "name": "Movies"})
    ET.SubElement(movies, "subcat", {"id": "2030", "name": "SD"})
    ET.SubElement(movies, "subcat", {"id": "2040", "name": "HD"})
    ET.SubElement(movies, "subcat", {"id": "2045", "name": "UHD"})
    tv = ET.SubElement(categories, "category", {"id": "5000", "name": "TV"})
    ET.SubElement(tv, "subcat", {"id": "5030", "name": "SD"})
    ET.SubElement(tv, "subcat", {"id": "5040", "name": "HD"})
    ET.SubElement(tv, "subcat", {"id": "5045", "name": "UHD"})
    ET.SubElement(tv, "subcat", {"id": "5070", "name": "Anime"})

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(caps, encoding="unicode")
