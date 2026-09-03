from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings
from src.clients.usage import (
    SERANKING_CREDITS_PER_RETURNED_KEYWORD, SERANKING_EXPORT_CREDITS, UsageTracker,
)

SERANKING_BASE_URL = "https://api.seranking.com/v1"


class SERankingClient:
    """Keyword Research API. Docs: https://seranking.com/api/data/keyword-research/

    keyword_metrics (demand validation) is flat 100 credits per request
    regardless of batch size (1-5000 keywords) - always batch the full
    candidate list into one call.

    related/questions bill 10 credits per RETURNED keyword. On the Wallet
    top-up plan ($50 = 250k credits, $0.0002/credit) that works out to
    ~$0.002/keyword, cheap enough to be worth their high real-demand hit
    rate (questions measured ~93%). longtail is deliberately absent -
    measured 0% real-demand hit rate across two separate tests."""

    def __init__(self, source: str = "us", usage: Optional[UsageTracker] = None):
        self._usage = usage
        self._client = httpx.Client(
            base_url=SERANKING_BASE_URL,
            headers={
                "Authorization": f"Token {settings.seranking_api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        self._source = source

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10),
           reraise=True)
    def keyword_metrics(self, keywords: list[str]) -> list[dict]:
        resp = self._client.post(
            "/keywords/export",
            params={"source": self._source},
            json={"keywords": keywords, "sort": "volume", "sort_order": "desc"},
        )
        resp.raise_for_status()
        if self._usage:
            # Flat rate - one 5000-keyword batch costs the same as one keyword.
            self._usage.record("seranking", "keywords/export", credits=SERANKING_EXPORT_CREDITS)
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10),
           reraise=True)
    def _discovery_get(self, path: str, keyword: str, limit: int) -> list[dict]:
        resp = self._client.get(
            path, params={"source": self._source, "keyword": keyword, "limit": limit}
        )
        resp.raise_for_status()
        keywords = (resp.json() or {}).get("keywords") or []
        if self._usage:
            # Billed per keyword RETURNED, so an empty response is free.
            self._usage.record(
                "seranking", path.strip("/"),
                credits=len(keywords) * SERANKING_CREDITS_PER_RETURNED_KEYWORD,
            )
        return keywords

    def related_keywords(self, keyword: str, limit: int = 15) -> list[dict]:
        """10 credits per returned keyword - limit is the cost dial."""
        return self._discovery_get("/keywords/related", keyword, limit)

    def question_keywords(self, keyword: str, limit: int = 15) -> list[dict]:
        """10 credits per returned keyword - limit is the cost dial."""
        return self._discovery_get("/keywords/questions", keyword, limit)
