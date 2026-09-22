import time
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings
from src.clients.usage import (
    SERANKING_CREDITS_PER_RETURNED_KEYWORD, SERANKING_EXPORT_CREDITS,
    SERANKING_SERP_CREDITS_PER_TASK, UsageTracker,
)

SERANKING_BASE_URL = "https://api.seranking.com/v1"

# US, matching the "us" source already used for keyword calls.
SERP_LOCATION_ID_US = 2840


class SERankingClient:
    """Keyword Research API. Docs: https://seranking.com/api/data/keyword-research/

    keyword_metrics (demand validation) is flat 100 credits per request
    regardless of batch size (1-5000 keywords) - always batch the full
    candidate list into one call.

    related/questions/similar bill 10 credits per RETURNED keyword. At $50
    for 250,000 credits ($0.0002/credit) that works out to ~$0.002/keyword,
    cheap enough to be worth their high real-demand hit rate (questions
    measured ~93%). longtail is deliberately absent - measured 0% real-
    demand hit rate across two separate tests.

    serp_search replaces what used to be Scrappa's SERP fetch - a flat 50
    credits/task, measured directly against the account's own credit
    counter (10752 -> 10802 for one call)."""

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

    def similar_keywords(self, keyword: str, limit: int = 15) -> list[dict]:
        """10 credits per returned keyword - limit is the cost dial. Replaces
        Scrappa's autocomplete BFS as the discovery volume driver (see
        discovery.py) - semantically similar keywords/synonyms/phrasings
        from SE Ranking's own keyword database, not literal Google
        Autocomplete suggestions (no equivalent exists in SE Ranking's API),
        but the closest available single-call source for breadth."""
        return self._discovery_get("/keywords/similar", keyword, limit)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _submit_serp_task(self, keyword: str, device: str, language_code: str) -> int:
        resp = self._client.post(
            "/serp/classic/tasks",
            json={
                "search_engine": "google",
                "device": device,
                "language_code": language_code,
                "location_id": SERP_LOCATION_ID_US,
                "query": keyword,
            },
        )
        resp.raise_for_status()
        return resp.json()[0]["id"]

    def _poll_serp_task(self, task_id: int, max_wait: int = 240, interval: int = 5) -> dict:
        """SERP tasks are queued, not synchronous - real tasks measured 7s to
        over 120s to complete (one timed out at the old 120s cap), hence the
        wider default. The task returns {"status": "processing"} while still
        running; anything else is the finished result."""
        elapsed = 0
        while True:
            resp = self._client.get("/serp/classic/tasks/results_advanced", params={"task_id": task_id})
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "processing":
                return data
            if elapsed >= max_wait:
                raise TimeoutError(f"SE Ranking SERP task {task_id} did not complete within {max_wait}s")
            time.sleep(interval)
            elapsed += interval

    def serp_search(
        self, keyword: str, device: str = "desktop", language_code: str = "en", max_attempts: int = 5,
    ) -> dict:
        """Replaces Scrappa's google_search - returns the same
        organic_results/search_information shape so check_serp() in
        serp_validation.py doesn't need to change beyond the client swap.

        Retries with a FRESH task submission on timeout, not more polling of
        the same task - measured directly: the identical query
        "AI tools submit your site directory" got stuck in "processing"
        indefinitely on one submission and completed normally in under 90s
        on the next, so the stuck state belongs to that one task, not the
        query. Each attempt uses a shorter 90s cap so 5 attempts stay
        bounded (~7.5min worst case); raised from 3 after a real run showed
        ~45% of individual tasks stuck on a bad day, making 3 consecutive
        failures non-negligible."""
        last_error: Optional[Exception] = None
        result = None
        for _ in range(max_attempts):
            task_id = self._submit_serp_task(keyword, device, language_code)
            if self._usage:
                self._usage.record("seranking", "serp/classic/tasks", credits=SERANKING_SERP_CREDITS_PER_TASK)
            try:
                result = self._poll_serp_task(task_id, max_wait=90)
                break
            except TimeoutError as e:
                last_error = e
        if result is None:
            raise last_error
        items = result.get("items") or []
        organic = [i for i in items if i.get("type") == "organic"]
        return {
            "organic_results": [
                {
                    "position": r.get("rank_absolute"),
                    "title": r.get("title"),
                    "link": r.get("url"),
                    "source": r.get("domain"),
                    "snippet": r.get("description"),
                }
                for r in organic
            ],
            "search_information": {"total_results": (result.get("summary") or {}).get("total_results")},
        }
