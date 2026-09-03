from pydantic import BaseModel, Field

from src.clients.deepseek_client import DeepSeekClient, BudgetExceeded

BATCH_SIZE = 100


class RelevanceOutput(BaseModel):
    relevant: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You remove obviously off-topic keywords from a list. This is a light \
cleanup pass, NOT a quality filter - a later stage scores each keyword properly.

DEFAULT TO KEEPING. Keep anything even loosely related to the website's subject area, \
including broad terms, competitor names, question-style keywords, informational queries, and \
keywords that seem too competitive. Those are all fine here.

Only DROP a keyword when it clearly belongs to a different subject entirely - the kind of \
drift that happens when a keyword tool expands an ambiguous seed. Concretely:
- job / salary / hiring / certification queries ("process automation engineer salary")
- image or photo searches ("communication devices images")
- a completely different profession or industry ("communication tools in nursing")
- non-English definition or translation queries ("time management meaning in hindi")

If you are unsure, KEEP it. Dropping a good keyword is far worse than keeping a mediocre one.
Expect to keep most of the list - if you are dropping more than about a third, you are being \
too strict.

Return the keywords you keep, copied EXACTLY as given (character for character - do not \
rewrite, reword, capitalize, or reformat). Return JSON matching the required schema."""


def filter_relevant(
    deepseek: DeepSeekClient, category: str, niche_name: str, keywords: list[str]
) -> list[str]:
    """Topical relevance gate. Runs after demand validation (so it only pays to
    check keywords that already have real search volume) and before ranking -
    which matters, because ranking by difficulty-ascending actively surfaces
    off-topic keywords first: irrelevant queries face no competition in this
    niche, so they score as 'easy' and get judged before the real candidates.

    Only keywords echoed back exactly are kept, so a hallucinated or reworded
    response can't inject keywords that were never in the candidate set."""
    kept: list[str] = []
    valid = set(keywords)

    for i in range(0, len(keywords), BATCH_SIZE):
        batch = keywords[i : i + BATCH_SIZE]
        user_prompt = (
            f"Website category: {category}\n"
            f"Niche: {niche_name}\n\n"
            f"Keywords to filter:\n" + "\n".join(f"- {kw}" for kw in batch)
        )
        try:
            result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, RelevanceOutput)
        except BudgetExceeded:
            raise
        except Exception:
            # If a batch fails, keep it rather than silently discarding real
            # candidates - the judge downstream is the backstop.
            kept.extend(batch)
            continue
        kept.extend(kw for kw in result.relevant if kw in valid)

    return kept
