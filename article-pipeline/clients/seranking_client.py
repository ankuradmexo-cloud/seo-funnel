import time
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

SERANKING_BASE_URL = "https://api.seranking.com/v1"

# US, matches the "us" source already used for keywords/export - all sites
# this pipeline publishes to are US-targeted.
SERP_LOCATION_ID_US = 2840

SERANKING_EXPORT_CREDITS = 100
# Measured directly: one real /serp/classic/tasks call moved the account's
# "used" counter from 10752 to 10802 - a flat 50 credits regardless of how
# many organic/PAA/related items came back.
SERANKING_SERP_CREDITS_PER_TASK = 50


class SERankingClient:
    """keywords/export (demand validation) and serp/classic (replaces
    Scrappa's SERP fetch - see serp_research.py). serp/classic is async:
    submit a task, then poll for completion; real tasks measured 7s to over
    120s to complete."""

    def __init__(self, source: str = "us"):
        self.calls_made = 0
        self.credits_used = 0
        self._client = httpx.Client(
            base_url=SERANKING_BASE_URL,
            headers={
                "Authorization": f"Token {settings.seranking_api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        self._source = source

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def keyword_metrics(self, keywords: list[str]) -> list[dict]:
        self.calls_made += 1
        resp = self._client.post(
            "/keywords/export",
            params={"source": self._source},
            json={"keywords": keywords, "sort": "volume", "sort_order": "desc"},
        )
        resp.raise_for_status()
        self.credits_used += SERANKING_EXPORT_CREDITS
        return resp.json()

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
        wider default. Returns {"status": "processing"} while still running
        (per SE Ranking's own n8n integration source); anything else is the
        finished result."""
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
        """Replaces Scrappa's google_search - same output shape research_serp.py
        expects (top_results/people_also_ask/related_searches/total_results),
        so callers don't need to change beyond swapping the client.

        Retries with a FRESH task submission on timeout, not more polling of
        the same task - measured directly: the identical query
        "AI tools submit your site directory" got stuck in "processing"
        indefinitely on one submission and completed normally in under 90s
        on the next, so the stuck state belongs to that one task, not the
        query. Each attempt uses a shorter 90s cap so 5 attempts stay
        bounded (~7.5min worst case); raised from 3 after a real run showed
        ~45% of individual tasks stuck on a bad day, making 3 consecutive
        failures non-negligible."""
        self.calls_made += 1
        last_error: Optional[Exception] = None
        result = None
        for _ in range(max_attempts):
            task_id = self._submit_serp_task(keyword, device, language_code)
            self.credits_used += SERANKING_SERP_CREDITS_PER_TASK  # billed on submission regardless of outcome
            try:
                result = self._poll_serp_task(task_id, max_wait=90)
                break
            except TimeoutError as e:
                last_error = e
        if result is None:
            raise last_error
        items = result.get("items") or []
        organic = [i for i in items if i.get("type") == "organic"]
        paa = [
            q
            for i in items if i.get("type") == "people_also_ask"
            for q in (i.get("searches") or [])
        ]
        related = [
            link.get("title")
            for i in items if i.get("type") == "related_searches"
            for link in (i.get("links") or [])
            if link.get("title")
        ]
        return {
            "keyword": keyword,
            "top_results": [
                {
                    "position": r.get("rank_absolute"),
                    "title": r.get("title"),
                    "link": r.get("url"),
                    "source": r.get("domain"),
                    "snippet": r.get("description"),
                }
                for r in organic
            ],
            "people_also_ask": paa,
            "related_searches": related,
            "total_results": (result.get("summary") or {}).get("total_results"),
        }
