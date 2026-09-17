"""Step 7 - content gap analysis.

Reasons over the full competitor content plus what's already been
established as core (common_content) and the topic's vocabulary - finding
gaps means finding what's weak or missing RELATIVE TO the core, not
rediscovering the core itself. This becomes the ~30% differentiation signal
that content_gap_analysis and trend research (step 8) together feed into
outline creation (step 9).
"""

from clients.deepseek_client import DeepSeekClient
from models import ContentGapAnalysis
from pipeline.competitor_scraping import format_competitors_for_prompt

SYSTEM_PROMPT = """Given the full content of what's currently ranking for a keyword, the core \
topics already identified as consensus coverage, and the topic's vocabulary, identify content \
gaps: topics, angles, or depth the ranking pages under-serve, OR information that looks outdated. \
Ground every gap in what the competitor content actually says or doesn't say - point at \
specifics, not a general impression, and don't re-list the core topics as gaps. Focus on gaps a \
genuinely useful, well-written article could fill - not just "say more words about the same \
thing". Then propose one clear differentiation angle for a new article entering this space.

Return JSON matching the required schema only."""


def analyze_content_gaps(
    deepseek: DeepSeekClient,
    keyword: str,
    competitor_research: dict,
    common_content: dict,
    vocabulary: dict,
    scraped_competitors: list[dict],
) -> dict:
    core_topics = "\n".join(f"  - {t['topic']}" for t in common_content.get("core_topics") or []) or "(none)"
    vocab_terms = ", ".join(t["term"] for t in vocabulary.get("terms") or []) or "(none)"

    user_prompt = (
        f"Keyword: {keyword}\n\n"
        f"Format overview: {competitor_research.get('dominant_format')} - "
        f"{competitor_research.get('format_rationale')}\n\n"
        f"Already-established core topics (do not re-list these as gaps):\n{core_topics}\n\n"
        f"Topic vocabulary already identified: {vocab_terms}\n\n"
        f"Full competitor content:\n{format_competitors_for_prompt(scraped_competitors)}"
    )
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, ContentGapAnalysis, label="content_gap_analysis")
    return result.model_dump()
