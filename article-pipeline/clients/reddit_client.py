"""Reddit search via the official OAuth API.

Previously scraped old.reddit.com/search.json unauthenticated - documented
in git history as "genuinely unstable" while building it, and measured
directly: blocked on every single run of a 5-keyword batch test. That path
has no ToS-sanctioned guarantee of staying open; Reddit can (and did)
tighten it without notice.

This uses the app-only client_credentials OAuth grant instead - a
registered "script" app's client_id/secret exchanged for a bearer token,
no Reddit username/password required. Free for this volume of read-only
public search (a handful of queries per pipeline run), unlike Reddit's paid
tier which gates high-volume commercial API use. Requires
REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET in .env (see config.py) - without
them, this degrades to the same empty-result behavior as before, exactly
as reddit_research.py already expects.
"""

import time
from typing import Optional

import httpx

from config import settings

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/search"

_token_cache: dict = {"access_token": None, "expires_at": 0.0}


def _get_access_token() -> Optional[str]:
    if not (settings.reddit_client_id and settings.reddit_client_secret):
        return None

    # Reuse the cached token until shortly before it expires (Reddit's
    # client_credentials tokens last ~1 hour) rather than re-authenticating
    # on every search call.
    if _token_cache["access_token"] and time.monotonic() < _token_cache["expires_at"] - 30:
        return _token_cache["access_token"]

    try:
        resp = httpx.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(settings.reddit_client_id, settings.reddit_client_secret),
            headers={"User-Agent": settings.reddit_user_agent},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        token = body.get("access_token")
        if not token:
            return None
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = time.monotonic() + body.get("expires_in", 3600)
        return token
    except Exception:
        return None


def search_reddit(query: str, limit: int = 15, timeframe: str = "year") -> list[dict]:
    token = _get_access_token()
    if not token:
        return []

    try:
        resp = httpx.get(
            SEARCH_URL,
            params={"q": query, "sort": "top", "t": timeframe, "limit": limit},
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": settings.reddit_user_agent,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        body = resp.json()
    except Exception:
        return []

    children = (body.get("data") or {}).get("children") or []
    threads = []
    for c in children:
        d = c.get("data") or {}
        if not d.get("title"):
            continue
        threads.append(
            {
                "title": d["title"],
                "subreddit": d.get("subreddit"),
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "permalink": f"https://reddit.com{d.get('permalink', '')}" if d.get("permalink") else None,
                "selftext": (d.get("selftext") or "")[:500],
            }
        )
    return threads
