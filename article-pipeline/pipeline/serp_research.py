"""Step 1 - SERP research.

Fetches the current top organic results, People Also Ask questions, and
related searches for the target keyword. This is fetched once, up front -
every later step that needs SERP data reads it from here rather than
re-querying SE Ranking.

SE Ranking's serp/classic task returns up to 100 organic results in one
call (measured: 69 for a real keyword), so unlike the old Scrappa-backed
version there's no need to page through multiple requests to reach top_n -
one task covers it.
"""

from clients.seranking_client import SERankingClient


def research_serp(seranking: SERankingClient, keyword: str, top_n: int) -> dict:
    result = seranking.serp_search(keyword)
    result["top_results"] = result["top_results"][:top_n]
    return result
