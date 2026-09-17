"""Step 8 - trend research synthesis.

Combines two signals: Reddit thread titles/engagement (genuine current
community activity, when available) and Google's People Also Ask + related
searches (step 1 - search-engine-derived, not activity-derived, but always
available). Reddit's public search endpoint is unauthenticated and can be
blocked at any time - if it returned nothing this run, this step still
works on PAA and related searches alone.

Twitter/X is deferred - pipeline/twitter_research.py always returns an
empty list (no free API path exists; see that file). This step is already
written to degrade gracefully on an empty source, so plugging in real
Twitter data later needs no change here - only twitter_research.py's stub.
"""

from clients.deepseek_client import DeepSeekClient
from models import CommunityResearch

# Measured directly: with zero real Reddit threads, the model still
# produced "What do users on Reddit say about X?" in real_questions_to_answer
# - reading the phrase "X reddit" in a related-search suggestion as if it
# were evidence people were actually discussing it. That question then
# flowed into the writer, which invented specific fake quotes and claims
# attributed to real Reddit users to answer it. The prompt now says not to
# do this, but an instruction alone isn't trusted anywhere else in this
# pipeline either - this is the same measure-and-correct pattern as
# length_control.py, applied here as a filter instead of a fix-up call
# since removing a fabricated question is safe and doesn't need an LLM
# round-trip.
_COMMUNITY_SENTIMENT_MARKERS = (
    "reddit", "redditor", "forum", "people say", "users say", "people online",
    "community say", "community think",
)


def _references_unavailable_community_data(question: str, reddit_threads: list[dict]) -> bool:
    if reddit_threads:
        return False
    q = question.lower()
    return any(marker in q for marker in _COMMUNITY_SENTIMENT_MARKERS)

SYSTEM_PROMPT = """People Also Ask questions and related searches reflect what searchers want \
answered for this keyword. Reddit threads (title, subreddit, score, comment count) reflect what \
people are ACTIVELY discussing right now - a genuinely current signal, not a search engine's \
guess. Use both, but weight Reddit's signal higher when it's present - it is real behavior, not \
inference.

Identify recurring themes, and extract the concrete real-world questions the article should \
directly answer. Separately, identify trending angles: specific framings, hooks, or phrasing \
patterns clearly resonating in the Reddit discussion right now - a pain point people keep \
raising, a myth being debunked, a comparison format getting engagement, a specific complaint or \
recommendation repeated across threads. Make these concrete and specific to this keyword, not \
generic - they will directly shape the article's title. If no Reddit threads are provided, leave \
trending_angles empty rather than inventing angles from PAA/related searches alone.

Google's People Also Ask routinely surfaces near-duplicate phrasings of the SAME underlying \
question - "creative ideas for X", "unique ideas for X", "easy ideas for X", "simple X ideas for \
beginners" are one question asked four ways, not four separate questions. Merge these into ONE \
representative question in real_questions_to_answer, phrased however captures the underlying need \
best - do not return near-synonyms as separate list items. This was measured to cause real \
damage downstream: four near-duplicate questions each got treated as a distinct requirement, and \
the section written to answer them ended up repeating the same handful of examples three times \
over with only the wrapper sentence changed, because each "different" question triggered its own \
answer. Two questions belong together if answering one well would already satisfy the other - not \
just if they share a topic; "how to make a scrapbook album" and "what supplies do I need" are \
about the same topic but are genuinely different questions and should stay separate.

Never generate a question framed as "what do Reddit users / the community say" (or similar - \
forum members, redditors, people online) unless real Reddit threads are provided above that \
actually support it. A related search containing the word "reddit" (e.g. "X reddit") only means \
people search that way to FIND Reddit discussion - it is not itself evidence of what was said, \
and is not a substitute for an actual thread. Turning it into a community-attributed question \
forces the article to answer it by inventing fake quotes and claims attributed to real users, \
which is worse than not covering it. If Reddit threads is empty, do not produce any question \
that presupposes community sentiment exists - a PAA-style factual question is fine, a "what do \
people say" one is not.

Return JSON matching the required schema only."""


def research_community(
    deepseek: DeepSeekClient, keyword: str, serp: dict, reddit_threads: list[dict],
) -> dict:
    paa = "\n".join(f"- {q}" for q in serp.get("people_also_ask") or []) or "(none returned)"
    related = "\n".join(f"- {q}" for q in serp.get("related_searches") or []) or "(none returned)"
    reddit_block = "\n".join(
        f"- [{t['score']} pts, {t['num_comments']} comments, r/{t['subreddit']}] {t['title']}"
        + (f" — {t['selftext'][:200]}" if t.get("selftext") else "")
        for t in reddit_threads
    ) or "(no Reddit threads found this run - endpoint may be rate-limited or blocked)"

    user_prompt = (
        f"Keyword: {keyword}\n\n"
        f"People Also Ask:\n{paa}\n\n"
        f"Related searches:\n{related}\n\n"
        f"Reddit discussion (sorted by engagement):\n{reddit_block}"
    )
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, CommunityResearch, label="community_research")
    data = result.model_dump()

    kept = [
        q for q in data.get("real_questions_to_answer") or []
        if not _references_unavailable_community_data(q, reddit_threads)
    ]
    data["real_questions_to_answer"] = kept
    if not reddit_threads:
        # Same reasoning as trending_angles in the prompt above - without
        # real threads, any framing this produces is inference dressed up
        # as observed behavior.
        data["trending_angles"] = []
    return data
