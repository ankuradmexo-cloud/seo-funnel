from src.clients.scrappa_client import ScrappaClient
from src.models.schemas import SerpResult, SerpSignal


def check_serp(scrappa: ScrappaClient, keyword: str) -> SerpSignal:
    """Deterministic fetch only - Scrappa doesn't return a domain-authority
    style score, so 'is there a realistic gap' is left to the SEO Judge (stage 7)
    to reason about from the actual titles/sources, not a fabricated heuristic.

    Back on Scrappa as of 2026-09-24 (was briefly on SE Ranking's serp/classic
    task) - measured directly, ~33x cheaper per check (1 credit flat vs 50
    credits/task), which matters a lot once every demand-validated candidate
    gets judged instead of stopping at the daily shortlist target."""
    search_result = scrappa.google_search(keyword)
    organic = (search_result.get("organic_results") or [])[:5]
    return SerpSignal(
        keyword=keyword,
        top_results=[
            SerpResult(
                position=r.get("position"),
                title=r.get("title"),
                link=r.get("link"),
                source=r.get("source"),
                snippet=r.get("snippet"),
            )
            for r in organic
        ],
        total_results=(search_result.get("search_information") or {}).get("total_results"),
    )
