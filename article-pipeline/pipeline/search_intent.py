"""Step 2 - search intent detection.

Runs immediately after SERP research and before any deep competitor
analysis. Everything downstream is meant to respect this: the writer's
BLUF ordering, whether the ~30% gap/trend content still has to satisfy it,
and whether the keyword is even worth writing an article for at all.

Judges intent from BOTH the keyword's own wording AND the SERP - not SERP
alone. That used to be SERP-only ("do not guess from the keyword's wording
alone"), on the reasoning that real ranking data beats guessing. Found by
testing that this makes the whole classification fragile to bad data:
Scrappa's SERP for "plus size clothing stores nearby" came back full of
unrelated results (an organization that happens to share the word "plus")
on one run, and a SERP-only classifier missed an obviously local-intent
keyword because the SERP that day was garbage - even though "nearby" in the
keyword itself is an unambiguous, SERP-independent signal. The keyword's
own plain meaning is now the primary signal; the SERP corroborates it or
flags a mismatch, it doesn't override it.
"""

from clients.deepseek_client import DeepSeekClient
from models import SearchIntent

SYSTEM_PROMPT = """Classify the search intent behind this keyword using BOTH the keyword's own \
wording and the SERP results provided - not the SERP alone. The keyword's own plain meaning is a \
real, reliable signal on its own: "near me", "nearby", "in [city]", "open now", "hours", \
"directions to" unambiguously mean local intent regardless of what happens to be ranking that \
day. Use the SERP to corroborate or add detail, and note if the SERP looks unrelated to the \
keyword (set serp_matches_keyword false in that case) - but a mismatched or low-quality SERP \
should never override what the keyword itself obviously means.

primary_intent is one of: informational (explaining/teaching), commercial (comparing/evaluating \
options before a decision), transactional (ready to buy/act now), navigational (looking for a \
specific site/brand), or mixed (genuinely split - say so rather than forcing a single label).

bluf_guidance must be concrete and specific to THIS keyword - not generic advice. State exactly \
what the article's opening sentence(s) and each section's opening sentence should lead with. \
Example for an informational "how does X work" keyword: "State the mechanism in one sentence \
before explaining the parts." Example for a commercial "best X for Y" keyword: "Name the top \
pick and the one-line reason before the comparison table."

suitable_for_article: set this to false for keywords where the real winning result is a Google \
Maps pack, a store-locator page, or a business directory listing - not written content. Judge \
this from the KEYWORD ITSELF first: "near me" / "nearby" / "in [city]" / "open now" / "hours" / \
"directions to" patterns mean a real searcher wants a place on a map, and no article can win that \
regardless of quality - set this false on that basis alone, you do not need the SERP to confirm \
it. If the SERP is also available and shows store locators or directory listings, that's further \
confirmation, not a requirement. Give a one-sentence unsuitability_reason when false. This is \
rare outside the local-intent pattern - most keywords, including most informational, commercial, \
and even many navigational ones, ARE suitable for an article. Default to true.

Return JSON matching the required schema only."""


def detect_search_intent(deepseek: DeepSeekClient, keyword: str, serp: dict) -> dict:
    listing = "\n".join(
        f"{r['position']}. {r['title']}\n   {r.get('snippet') or '(no snippet)'}"
        for r in serp.get("top_results") or []
    ) or "(no organic results returned)"
    user_prompt = f"Keyword: {keyword}\n\nTop organic results:\n{listing}"
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, SearchIntent, label="search_intent")
    return result.model_dump()
