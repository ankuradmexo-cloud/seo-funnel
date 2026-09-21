import httpx

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


def discover_keywords(
    seranking: SERankingClient, seed_keyword: str
) -> list[DiscoveryCandidate]:
    """Three measured discovery sources, each with a distinct profile:

    - similar (SE Ranking, 10 credits/returned keyword): replaces the old
      Scrappa autocomplete BFS as the volume driver - semantically similar
      keywords/synonyms/phrasings from SE Ranking's own keyword database
      rather than literal Google Autocomplete suggestions (no equivalent
      exists in SE Ranking's API). A single flat call per seed, not a BFS.
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

    if settings.similar_limit_per_seed > 0:
        try:
            for item in seranking.similar_keywords(seed_keyword, limit=settings.similar_limit_per_seed):
                if item.get("keyword"):
                    candidates.setdefault(item["keyword"], set()).add("similar")
        except httpx.HTTPError as e:
            if not _is_transient(e):
                raise
            pass  # one source failing shouldn't lose the other two

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
