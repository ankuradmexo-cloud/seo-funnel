import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

SCRAPPA_BASE_URL = "https://scrappa.co/api"

# Measured directly, 2026-09-24: Scrappa bills 1 credit/request flat
# regardless of results returned - $10 = 33,000 credits ($0.0003/call),
# roughly 33x cheaper per SERP check than SE Ranking's serp/classic task
# (50 credits flat, $0.01/check). Revived after that comparison.
SCRAPPA_CREDITS_PER_CALL = 1
SCRAPPA_COST_PER_CREDIT = 10 / 33_000


class ScrappaClient:
    """SERP data only - no full-page scraping endpoint exists on Scrappa, so
    competitor research (pipeline/competitor_research.py) works from titles
    and snippets, not full article text."""

    def __init__(self):
        self.calls_made = 0
        self._client = httpx.Client(
            base_url=SCRAPPA_BASE_URL,
            headers={"x-api-key": settings.scrappa_api_key},
            timeout=30,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def google_search(self, query: str, page: int = 0) -> dict:
        """Full Google Search result - organic_results, related_searches, and
        related_questions (People Also Ask) all come from this one call.
        page is 0-based (page=1 fetches the second page of organic results),
        used by serp_research.py to pull enough raw results that filtering
        out non-article competitors (App Store listings, forums) still
        leaves a usable-sized pool."""
        self.calls_made += 1
        resp = self._client.get("/search", params={"query": query, "page": page})
        resp.raise_for_status()
        return resp.json()

    def people_also_ask(self, search_result: dict) -> list[str]:
        return [
            q["question"]
            for q in search_result.get("related_questions") or []
            if q.get("question")
        ]

    def related_searches(self, search_result: dict) -> list[str]:
        return [
            r["query"]
            for r in search_result.get("related_searches") or []
            if r.get("query")
        ]
