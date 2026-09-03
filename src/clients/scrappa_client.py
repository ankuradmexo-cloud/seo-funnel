from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings
from src.clients.usage import SCRAPPA_CREDITS_PER_CALL, UsageTracker

SCRAPPA_BASE_URL = "https://scrappa.co/api"


class ScrappaClient:
    def __init__(self, usage: Optional[UsageTracker] = None):
        self._usage = usage
        self._client = httpx.Client(
            base_url=SCRAPPA_BASE_URL,
            headers={"x-api-key": settings.scrappa_api_key},
            timeout=30,
        )

    def _track(self, endpoint: str) -> None:
        """Recorded before raise_for_status: Scrappa bills per request, and a
        request that comes back 5xx has still been made."""
        if self._usage:
            self._usage.record("scrappa", endpoint, credits=SCRAPPA_CREDITS_PER_CALL)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10),
           reraise=True)
    def autocomplete(self, query: str) -> list[str]:
        resp = self._client.get("/search-light/autocomplete", params={"query": query})
        self._track("autocomplete")
        resp.raise_for_status()
        return resp.json().get("suggestions") or []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10),
           reraise=True)
    def google_search(self, query: str) -> dict:
        """Full Google Search result. related_searches, related_questions (People
        Also Ask), and organic_results all come back from this single call - reuse
        it rather than hitting /search again for the same query."""
        resp = self._client.get("/search", params={"query": query})
        self._track("search")
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
