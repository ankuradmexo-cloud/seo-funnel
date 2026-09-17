"""Step 8a - Reddit research.

Fetches real, currently-active Reddit discussion for the keyword: thread
titles, subreddit, and engagement (score, comment count). This is genuine
current community signal, unlike Google's PAA/related searches (step 1),
which are search-engine-derived, not activity-derived.

Best-effort - see clients/reddit_client.py for why. An empty result here
just means this run has no Reddit signal; the next step still works fine on
PAA/related searches alone.
"""

from clients.reddit_client import search_reddit


def research_reddit(keyword: str, limit: int) -> list[dict]:
    threads = search_reddit(keyword, limit=limit)
    return sorted(threads, key=lambda t: t["score"], reverse=True)
