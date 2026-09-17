"""Step 13 - SEO and content QA.

This is a sanity checker, not the final judge - the actual SEO score comes
from pasting the article into SE Ranking's Content Editor in the browser
(see README). This step catches structural problems that scorer won't
directly flag: missing sections, unanswered PAA questions, keyword stuffing
or omission.
"""

from clients.deepseek_client import DeepSeekClient
from models import SeoQaReport

SYSTEM_PROMPT = """Review this article against its brief. Check whether every outline section \
is actually present and substantive (not just mentioned in passing), whether the real questions \
the article was supposed to answer are actually answered somewhere in the text, and whether the \
assigned keywords appear naturally rather than being stuffed or completely absent. List concrete \
issues with a severity and a specific suggested fix for each - not generic advice.

Return JSON matching the required schema only."""


def run_qa(
    deepseek: DeepSeekClient,
    keyword: str,
    article: str,
    outline: dict,
    real_questions: list[str],
    section_keyword_map: dict,
) -> dict:
    sections = "\n".join(f"- {s['heading']}" for s in outline["sections"])
    questions = "\n".join(f"- {q}" for q in real_questions) or "(none)"
    assignments = "\n".join(
        f"- {a['section_heading']}: {', '.join(k['keyword'] for k in a['keywords'])}"
        for a in section_keyword_map.get("assignments") or []
    ) or "(none)"

    user_prompt = (
        f"Keyword: {keyword}\n\n"
        f"Required sections:\n{sections}\n\n"
        f"Required questions to answer:\n{questions}\n\n"
        f"Keyword assignments per section:\n{assignments}\n\n"
        f"--- Article ---\n{article}\n--- end ---"
    )
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, SeoQaReport, label="seo_qa")
    return result.model_dump()
