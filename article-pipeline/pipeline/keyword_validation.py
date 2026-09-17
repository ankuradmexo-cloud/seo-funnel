"""Step 11 - bulk keyword validation.

Reuses SE Ranking's keywords/export endpoint - the same flat-rate batch call
the keyword funnel uses for demand validation: 100 credits regardless of
batch size, so every section's candidates go in ONE call, not one per
section. Field mapping is copied from the funnel's proven
demand_validation.py rather than guessed.
"""

from clients.seranking_client import SERankingClient


def validate_keywords(seranking: SERankingClient, candidates: list[str]) -> list[dict]:
    if not candidates:
        return []
    results = seranking.keyword_metrics(candidates)
    validated = [
        {
            "keyword": item["keyword"],
            "search_volume": item.get("volume") if item.get("is_data_found") else None,
            "cpc": item.get("cpc"),
            "competition": item.get("competition"),
            "difficulty": item.get("difficulty"),
            "intents": item.get("intents") or [],
        }
        for item in results
    ]
    # Only keywords with real, measured demand are worth handing to the
    # writer - a keyword with no volume data offers nothing over the outline
    # as written.
    return [v for v in validated if v["search_volume"]]


def regroup_by_section(section_candidates: list[dict], validated: list[dict]) -> dict:
    """Reattaches validated keywords to the section that generated them.

    This is a lookup, not a judgment call - it replaces the old
    section_mapping.py LLM step, which had to *guess* which section a
    keyword belonged to after the fact. Per-section expansion (step 10)
    means that's already known; this just carries it through. Matched by
    normalized (lowercased, trimmed) keyword text, since SE Ranking's
    response isn't guaranteed to preserve the exact casing/whitespace of
    what was sent.
    """
    def norm(s: str) -> str:
        return s.strip().lower()

    validated_by_keyword = {norm(v["keyword"]): v for v in validated}
    assignments = []
    for sc in section_candidates:
        matched = [
            validated_by_keyword[norm(c)]
            for c in sc["candidates"]
            if norm(c) in validated_by_keyword
        ]
        if matched:
            assignments.append({"section_heading": sc["section_heading"], "keywords": matched})
    return {"assignments": assignments}
