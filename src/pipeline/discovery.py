import httpx

from src.clients.scrappa_client import ScrappaClient
from src.clients.seranking_client import SERankingClient
from src.config import settings
from src.models.schemas import DiscoveryCandidate


def _is_transient(exc: Exception) -> bool:
    """Server-side hiccups and network trouble are worth skipping past; a 4xx
    is not. A bad key or an exhausted quota returns 401/402/429 on every seed,
    and swallowing that would let a run finish "successfully" with zero
    keywords - which then retires niches for a fault that is not theirs."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _autocomplete_expand(scrappa: ScrappaClient, seed: str, depth: int = 2, breadth: int = 10) -> set[str]:
    """BFS out from the seed via Scrappa autocomplete. Each call is 1 credit and
    returns up to 10 keywords, so recursing one extra level multiplies breadth
    cheaply: depth=2, breadth=10 is up to 11 calls per seed."""
    seen: set[str] = set()
    frontier = [seed]
    for _ in range(depth):
        next_frontier: list[str] = []
        for query in frontier:
            try:
                suggestions = scrappa.autocomplete(query)[:breadth]
            except httpx.HTTPError as e:
                if not _is_transient(e):
                    raise  # bad key / out of credits - fail loudly
                continue  # transient 5xx or timeout - skip this branch
            for s in suggestions:
                if s not in seen:
                    seen.add(s)
                    next_frontier.append(s)
        frontier = next_frontier
    seen.discard(seed)
    return seen


def discover_keywords(
    scrappa: ScrappaClient, seranking: SERankingClient, seed_keyword: str
) -> list[DiscoveryCandidate]:
    """Three measured discovery sources, each with a distinct profile:

    - autocomplete (Scrappa, ~$0.0003/call for up to 10 keywords): ~28-31% of
      results have real search volume, and it produced the best approval yield
      of anything tested. The volume driver.
    - questions (SE Ranking, 10 credits/returned keyword): ~93% real-volume hit
      rate - very little noise, but only when given SHORT head-term seeds.
    - related (SE Ranking, 10 credits/returned keyword): ~98% real-volume hit
      rate, but skews to broad head terms that are usually too competitive.
      Kept behind a config toggle so it can be dropped if it keeps failing
      at the judge stage.

    LLM bulk generation is deliberately absent: measured 0-0.8% real-volume hit
    rate across four separate tests, while dominating runtime and token spend.
    """
    candidates: dict[str, set[str]] = {}

    for kw in _autocomplete_expand(scrappa, seed_keyword):
        candidates.setdefault(kw, set()).add("autocomplete")

    if settings.questions_limit_per_seed > 0:
        try:
            for item in seranking.question_keywords(seed_keyword, limit=settings.questions_limit_per_seed):
                if item.get("keyword"):
                    candidates.setdefault(item["keyword"], set()).add("questions")
        except httpx.HTTPError as e:
            if not _is_transient(e):
                raise
            pass  # one source failing shouldn't lose the other two

    if settings.related_limit_per_seed > 0:
        try:
            for item in seranking.related_keywords(seed_keyword, limit=settings.related_limit_per_seed):
                if item.get("keyword"):
                    candidates.setdefault(item["keyword"], set()).add("related")
        except httpx.HTTPError as e:
            if not _is_transient(e):
                raise

    return [
        DiscoveryCandidate(keyword=kw, source=sorted(sources))
        for kw, sources in candidates.items()
    ]
