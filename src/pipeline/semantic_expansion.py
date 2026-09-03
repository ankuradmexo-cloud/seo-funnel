from src.clients.deepseek_client import DeepSeekClient
from src.models.schemas import SemanticExpansionOutput

SYSTEM_PROMPT = """You explore the semantic neighborhood around a PROVEN keyword (one that \
already scored well / ranks / was published) to find ADJACENT search intents worth their own \
article - different use cases, audiences, problems, constraints, comparisons, or subtopics. \
Do NOT produce simple synonyms or near-duplicate phrasings of the anchor (e.g. "for beginners" \
vs "for new runners" are the same intent - skip those). Given the anchor and keywords already \
known for this niche, propose new adjacent intents not already covered. Set exhausted=true if \
you cannot find any more genuinely distinct intents worth exploring. Return JSON matching the \
required schema only."""


def expand_from_anchor(
    deepseek: DeepSeekClient, anchor_keyword: str, known_keywords: list[str]
) -> SemanticExpansionOutput:
    user_prompt = (
        f"Anchor (proven) keyword: {anchor_keyword}\n"
        f"Keywords already known for this niche:\n"
        + "\n".join(f"- {k}" for k in known_keywords)
    )
    return deepseek.structured_call(SYSTEM_PROMPT, user_prompt, SemanticExpansionOutput)


def expand_until_dry(
    deepseek: DeepSeekClient,
    anchor_keyword: str,
    known_keywords: list[str],
    max_rounds: int = 5,
) -> list[str]:
    """Loop-until-dry: keep expanding until the judge says there's nothing new left."""
    discovered: list[str] = []
    seen = set(known_keywords)

    for _ in range(max_rounds):
        result = expand_from_anchor(deepseek, anchor_keyword, list(seen))
        new = [k for k in result.adjacent_intents if k not in seen]
        if not new or result.exhausted:
            discovered.extend(new)
            break
        seen.update(new)
        discovered.extend(new)

    return discovered
