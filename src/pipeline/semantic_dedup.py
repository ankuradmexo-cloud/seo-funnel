from pydantic import BaseModel, Field

from src.clients.deepseek_client import DeepSeekClient, BudgetExceeded

BATCH_SIZE = 100


class DedupGroup(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class SemanticDedupOutput(BaseModel):
    groups: list[DedupGroup] = Field(default_factory=list)


SYSTEM_PROMPT = """You group keywords that would produce the SAME article if each were written up \
separately - same search intent, same answer, just different wording. This runs AFTER exact/stemmed \
deduplication, so anything left is not a literal word-order or plural/tense variant - it's a case that \
needs judgment: synonyms, rephrasing, or one keyword being a narrower slice of another that doesn't \
justify its own dedicated page.

Group keywords together ONLY when a single article would genuinely satisfy both searches equally well. \
Concretely, group:
- Synonymous phrasing: "how to meet solo travelers" / "how to meet other solo travelers"
- A generic keyword and a near-identical narrower one with no real added angle: "solo travel tips" / \
  "solo travel tips for introverts" / "solo travel tips for beginners" (these three answer the same \
  question with only cosmetic framing differences)
- Same question asked two ways: "what is couchsurfing" / "is couchsurfing worth it" is NOT the same \
  (one is definitional, one is evaluative) - do not group these

DO NOT group keywords that differ in real user intent, even if topically related:
- Different sub-topics of the same theme ("best offline travel apps" vs "best travel planning apps")
- A comparison vs a single-item query ("best esim for travel" vs "what is an esim")
- Different specificity that changes the searcher's actual need ("travel apps" vs "travel apps for \
  backpackers in southeast asia" - the second has real, distinct long-tail intent worth its own page)

DEFAULT TO NOT GROUPING. Only group when you're confident one article would genuinely serve both \
searches - a missed group just means two similar articles get written; a false group means a real \
long-tail opportunity gets silently thrown away. If in doubt, leave both keywords out of any group.

Every keyword you place in a group must appear copied EXACTLY as given (character for character - do \
not rewrite, reword, capitalize, or reformat). A keyword not in any group is left alone - do not create \
single-keyword groups. Return JSON matching the required schema."""


def semantic_dedup(
    deepseek: DeepSeekClient,
    category: str,
    niche_name: str,
    keywords: list[str],
    volume_by_keyword: dict[str, int],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Groups keywords that would produce duplicate articles despite surviving
    exact_dedup's word-order/stemmed matching (normalize.py) - synonyms and
    rephrasing that deterministic matching structurally can't catch, e.g.
    "solo travel tips" / "solo travel tips for introverts" / "solo travel tips
    for beginners" all reaching the judge as three separate keywords.

    Runs AFTER demand validation so every candidate has a real search_volume -
    within each LLM-proposed group, the highest-volume keyword survives and
    the rest are dropped, rather than an arbitrary pick.

    Deliberately mirrors relevance_filter.py's pattern rather than the
    dictionary/stemming approach in normalize.py: this catches what exact
    matching cannot (real synonyms, rephrasing), at the cost of being
    non-deterministic and consuming a DeepSeek call. Every dropped keyword is
    still recorded (status='rejected', not deleted) so a bad grouping is
    auditable and reversible. A keyword only echoed back exactly is trusted -
    a hallucinated or reworded response can't invent a group.

    Returns (survivors, dropped) where dropped is [(dropped_keyword,
    kept_keyword_it_duplicates), ...]."""
    valid = set(keywords)
    dropped: list[tuple[str, str]] = []
    dropped_set: set[str] = set()

    for i in range(0, len(keywords), BATCH_SIZE):
        batch = keywords[i : i + BATCH_SIZE]
        user_prompt = (
            f"Website category: {category}\n"
            f"Niche: {niche_name}\n\n"
            f"Keywords:\n" + "\n".join(f"- {kw}" for kw in batch)
        )
        try:
            result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, SemanticDedupOutput)
        except BudgetExceeded:
            raise
        except Exception:
            # A failed batch keeps every keyword rather than silently
            # discarding real candidates - the judge downstream is the
            # backstop, same fallback relevance_filter.py uses.
            continue

        for group in result.groups:
            members = [
                kw for kw in dict.fromkeys(group.keywords)  # de-dupe, preserve order
                if kw in valid and kw in batch and kw not in dropped_set
            ]
            if len(members) < 2:
                continue
            survivor = max(members, key=lambda kw: volume_by_keyword.get(kw, 0) or 0)
            for kw in members:
                if kw != survivor:
                    dropped.append((kw, survivor))
                    dropped_set.add(kw)

    survivors = [kw for kw in keywords if kw not in dropped_set]
    return survivors, dropped
