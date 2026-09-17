"""Assigns each article to one of its website's own pre-defined categories
(the comma-separated websites.category field that already drives niche
discovery - "AI tools, business/SaaS software comparisons, productivity
software, ..." - a small, deliberate, stable list, not something that grows
per-article). Two earlier approaches were tried and rejected: an LLM
free-inventing a category came out too narrow/inconsistent ("Payroll
Software" instead of a reusable "Software" bucket), and using the
keyword's niche name directly would sprawl into one WordPress category per
niche as niches keep growing, defeating the point of a category being a
small, stable navigation structure.
"""

from clients.deepseek_client import DeepSeekClient
from models import CategoryChoice

SYSTEM_PROMPT = """You are assigning a WordPress category to an article, from a FIXED list of \
categories that already exist for this website - do not invent a new one.

Return the single category from the list that best fits this article's actual topic. Copy it \
EXACTLY as given in the list - same wording, same casing. If genuinely none fit well, return the \
single closest one anyway - every article needs to land in one of these categories, there is no \
"none of the above" option.

Return JSON matching the required schema only."""


def choose_category(deepseek: DeepSeekClient, title: str, keyword: str, available_categories: list[str]) -> str:
    """available_categories: parsed from websites.category (split on comma).
    Always returns one of the given strings verbatim - falls back to the
    first category in the list if the model's answer doesn't exactly match
    one (rare, but a fixed fallback beats silently dropping the category)."""
    category_block = "\n".join(f"- {c}" for c in available_categories)
    user_prompt = (
        f"Keyword: {keyword}\nArticle title: {title}\n\n"
        f"Available categories for this website:\n{category_block}"
    )
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, CategoryChoice, label="categorize")
    chosen = result.model_dump()["category"].strip()

    for c in available_categories:
        if c.strip().lower() == chosen.lower():
            return c
    return available_categories[0]
