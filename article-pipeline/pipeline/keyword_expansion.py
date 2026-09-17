"""Step 10 - per-section keyword expansion.

Generates candidate keywords directly for each outline section, rather than
one global batch generated after the fact and mapped onto sections by a
separate LLM call (the old design: keyword_expansion.py -> section_mapping.py).
A candidate generated this way is already grounded in what that specific
section covers - "how it fits" isn't a guess made after the fact, it's how
the candidate was produced. This also means no separate mapping step exists
anymore; validation (step 11) works from the section-tagged candidates
directly.
"""

from clients.deepseek_client import DeepSeekClient
from models import SectionKeywordExpansion

SYSTEM_PROMPT = """Given an article outline, generate candidate long-tail keyword phrases FOR \
EACH SECTION individually - phrases that section's specific content would naturally cover, not \
generic variations of the main keyword repeated across every section. 2-6 words each. A \
section's candidates should reflect what THAT section is about; sections with little natural \
keyword opportunity can have few or none rather than being padded.

Return JSON matching the required schema only."""


def expand_keywords_per_section(
    deepseek: DeepSeekClient, keyword: str, outline: dict, max_per_section: int,
) -> list[dict]:
    section_listing = "\n".join(f"- {s['heading']}: {s['covers']}" for s in outline["sections"])
    user_prompt = (
        f"Primary keyword: {keyword}\n\n"
        f"Sections:\n{section_listing}\n\n"
        f"Up to {max_per_section} candidates per section."
    )
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, SectionKeywordExpansion, label="keyword_expansion")
    return result.model_dump()["sections"]
