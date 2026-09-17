import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

SERANKING_BASE_URL = "https://api.seranking.com/v1"


class SERankingClient:
    """keywords/export only - the same flat-rate batch endpoint the keyword
    funnel uses for demand validation: 100 credits regardless of batch size
    (1-5000 keywords), so the whole candidate list goes in one call."""

    def __init__(self, source: str = "us"):
        self.calls_made = 0
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
        return resp.json()
