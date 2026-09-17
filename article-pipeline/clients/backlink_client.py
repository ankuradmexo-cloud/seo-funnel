"""SE Ranking's Backlinks Data API - separate product/endpoint family from
seranking_client.py's keyword export, but same account/API key. Works
against ANY domain or URL, not just your own tracked SE Ranking projects -
what makes it usable for competitor analysis rather than only self-audits.

Real cost: 1 credit per backlink row returned (not a flat rate like
keywords/export) - callers control spend directly via `limit`.
"""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

BACKLINKS_URL = "https://api.seranking.com/v1/backlinks/all"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def get_backlinks(target: str, mode: str = "url", limit: int = 50) -> list[dict]:
    """target: a full URL (mode="url") or bare domain (mode="domain").
    per_domain=1 keeps only the single strongest backlink per referring
    domain - without it, one linking domain with many pages pointing at the
    same target would flood and skew the result."""
    resp = httpx.get(
        BACKLINKS_URL,
        params={
            "apikey": settings.seranking_api_key,
            "target": target,
            "mode": mode,
            "limit": limit,
            "order_by": "domain_inlink_rank",
            "per_domain": 1,
            "output": "json",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return (resp.json() or {}).get("backlinks") or []
