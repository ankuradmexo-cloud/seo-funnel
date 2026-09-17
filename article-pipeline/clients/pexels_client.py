"""Pexels photo search - the article's hero image.

Free tier, no per-image cost (200 requests/hour, 20,000/month - nowhere
near this pipeline's volume at one search per article). Degrades to None
on any failure or missing key, the same best-effort pattern as Reddit -
a run should never fail because a stock photo search didn't work.

Pexels' image CDN (images.pexels.com) accepts w/h/fit query params for
on-the-fly resizing - requesting a specific 1200x600 crop from the
original doesn't need a separate "sizes" negotiation.
"""

from typing import Optional

import httpx

from config import settings

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
HERO_WIDTH = 1200
HERO_HEIGHT = 600


def search_hero_image(query: str) -> Optional[dict]:
    if not settings.pexels_api_key:
        return None
    try:
        resp = httpx.get(
            PEXELS_SEARCH_URL,
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": settings.pexels_api_key},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        photos = (resp.json() or {}).get("photos") or []
        if not photos:
            return None
        photo = photos[0]
        original_url = photo["src"]["original"]
        sep = "&" if "?" in original_url else "?"
        hero_url = f"{original_url}{sep}auto=compress&cs=tinysrgb&fit=crop&w={HERO_WIDTH}&h={HERO_HEIGHT}"
        return {
            "url": hero_url,
            "alt": photo.get("alt") or query,
            "photographer": photo.get("photographer"),
            "photographer_url": photo.get("photographer_url"),
            "pexels_url": photo.get("url"),
        }
    except Exception:
        return None
