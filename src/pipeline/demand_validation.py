from src.clients.seranking_client import SERankingClient
from src.models.schemas import DemandMetrics


def validate_demand(seranking: SERankingClient, keywords: list[str]) -> list[DemandMetrics]:
    """One batched call for the whole candidate set - flat 100 credits regardless
    of how many keywords, so there's no reason to call this per-keyword."""
    results = seranking.keyword_metrics(keywords)
    return [
        DemandMetrics(
            keyword=item["keyword"],
            search_volume=item.get("volume") if item.get("is_data_found") else None,
            cpc=item.get("cpc"),
            competition=item.get("competition"),
            difficulty=item.get("difficulty"),
            intents=item.get("intents") or [],
            history_trend=item.get("history_trend") or {},
        )
        for item in results
    ]
