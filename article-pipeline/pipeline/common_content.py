"""Step 5 - common content (the core, ~70% signal).

Finds the topics/subtopics covered by most or all of the scraped
competitors - the consensus content a small site cannot afford to skip,
because every competitor already treats it as table-stakes for this
keyword. Distinct from content gap analysis (step 7), which finds what's
MISSING; this finds what's PRESENT everywhere and therefore mandatory.

The ~70/30 split between this step's output and step 7's is a soft,
situational ratio (per the plan) - this step doesn't enforce a percentage,
it just reports what's genuinely common, with a competitor count as
evidence for each topic so "core" claims are checkable, not asserted.
"""

from clients.deepseek_client import DeepSeekClient
from models import CommonContentAnalysis
from pipeline.competitor_scraping import format_competitors_for_prompt

SYSTEM_PROMPT = """Given the full content and heading structure of multiple competitors ranking \
for a keyword, identify the topics and subtopics that are covered by MOST or ALL of them - the \
consensus content. For each core topic, count how many of the competitors actually cover it \
(covered_by_count) and explain why a new article on this keyword cannot skip it.

Do not include a topic covered by only one competitor - that's a differentiator, not core \
content. Do not pad the list with generic topics no competitor actually addresses.

Return JSON matching the required schema only."""


def find_common_content(deepseek: DeepSeekClient, keyword: str, scraped_competitors: list[dict]) -> dict:
    user_prompt = f"Keyword: {keyword}\n\nCompetitors:\n{format_competitors_for_prompt(scraped_competitors)}"
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, CommonContentAnalysis, label="common_content")
    return result.model_dump()
