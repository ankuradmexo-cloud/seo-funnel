"""Step 1 - SERP research.

Fetches the current top organic results, People Also Ask questions, and
related searches for the target keyword. This is fetched once, up front -
every later step that needs SERP data reads it from here rather than
re-querying Scrappa.

Back on Scrappa as of 2026-09-24 (was briefly on SE Ranking's serp/classic
task) - measured directly, Scrappa's flat 1-credit/request SERP fetch is
~33x cheaper than SE Ranking's 50-credit/task serp/classic, with comparable
real yield once combined with SE Ranking's `questions` for precision.

A single Google results page only has ~9-10 organic results, and a chunk
of those are routinely non-article pages (App Store/Play Store listings,
forum threads) that competitor_scraping.py excludes from the structural
targets. Measured directly: one run's page-1-only fetch left just 3
competitors after filtering, versus a real SE Ranking brief drawing on a
much larger pool - so this pages through Scrappa's results (page=0, 1, ...)
until top_n raw results are collected or MAX_SERP_PAGES is hit, rather than
trusting a single page to contain enough usable competitors.
"""

from clients.scrappa_client import ScrappaClient

MAX_SERP_PAGES = 3


def research_serp(scrappa: ScrappaClient, keyword: str, top_n: int) -> dict:
    organic: list = []
    first_result = None
    for page in range(MAX_SERP_PAGES):
        if len(organic) >= top_n:
            break
        result = scrappa.google_search(keyword, page=page)
        if first_result is None:
            first_result = result
        page_organic = result.get("organic_results") or []
        if not page_organic:
            break
        organic.extend(page_organic)
    organic = organic[:top_n]
    result = first_result or {}
    return {
        "keyword": keyword,
        "top_results": [
            {
                "position": r.get("position"),
                "title": r.get("title"),
                "link": r.get("link"),
                "source": r.get("source"),
                "snippet": r.get("snippet"),
            }
            for r in organic
        ],
        "people_also_ask": scrappa.people_also_ask(result),
        "related_searches": scrappa.related_searches(result),
        "total_results": (result.get("search_information") or {}).get("total_results"),
    }
