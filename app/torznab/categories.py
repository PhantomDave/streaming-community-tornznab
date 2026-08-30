from __future__ import annotations


def category_for_release(source: str, sc_type: str, resolution: int) -> int:
    is_tv = sc_type.lower() == "tv"
    if source == "animeunity" and is_tv:
        return 5070
    if resolution >= 2160:
        return 5045 if is_tv else 2045
    if resolution >= 720:
        return 5040 if is_tv else 2040
    return 5030 if is_tv else 2030
