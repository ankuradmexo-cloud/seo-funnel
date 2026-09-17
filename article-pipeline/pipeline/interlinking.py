"""Inserts links from a NEW article to already-published articles on the
same WordPress site. Same "measure, don't just instruct" shape as
article_writer.py's _fix_unanswered_questions: the LLM only proposes
{anchor_text, url} pairs, never rewrites the article itself, and code
deterministically applies them - this is what keeps a hallucinated URL or
an LLM-rewritten sentence out of a real WordPress post.

No candidates (a brand-new site, nothing published yet) means no LLM call -
same degrade-to-empty pattern as pexels_client.py when a key is missing.
"""

import re

from config import settings
from clients.deepseek_client import DeepSeekClient
from models import InternalLinkingResult

# Found by testing a real 12-article batch: the model's most common failure
# mode was NOT inventing text, it was picking the CANDIDATE's own title as
# anchor_text (a phrase that exists in the destination article, not the one
# being written) - one run proposed 5 links this way and all 5 were
# correctly rejected for not matching. The instructions below call that
# exact mistake out by name, and the second failure mode - forcing a link
# to the only available candidate even when it's a weak topical fit (e.g.
# linking "credential stuffing" in a password-manager article to an
# unrelated CRM-software article) - gets an explicit "skip, don't force" rule.
SYSTEM_PROMPT = """You are choosing internal links for an SEO article, from a fixed list of \
already-published articles on the same website. Your job is NOT to write or rewrite any text - \
only to pick short phrases that ALREADY EXIST verbatim in the article below, and pair each one \
with the single most topically relevant candidate URL.

Rules:
- anchor_text must be copied EXACTLY (same casing, same words) from the ARTICLE TEXT BELOW - the \
one you are linking FROM, not the destination article. The single most common mistake is picking \
a candidate's own title as the anchor text instead of a phrase that actually appears in the \
article below - do not do this. If you cannot find a real phrase in the article below that fits a \
candidate, skip that candidate entirely rather than guessing.
- url must be exactly one of the candidate URLs given - never invent or guess a URL.
- Only propose a link where the candidate article is genuinely, specifically relevant to that \
exact anchor phrase's topic - not just "same general category." A forced link to the only \
available candidate, when nothing is really relevant, is worse than no link at all. When in \
doubt, leave it out.
- Propose at most 5 links total, and never propose the same url twice.
- If nothing in the article is a good fit for any candidate, return an empty list. That is a \
correct answer, not a failure.

Return JSON matching the required schema only."""


def _find_anchor(haystack: str, anchor: str) -> tuple[int, str]:
    """Case-insensitive search that still returns the ACTUAL substring and
    its casing from haystack (never the LLM's own casing of the anchor) -
    a real anchor that only differs in case from the article's text
    shouldn't be thrown away, but the article's own wording must not change.
    Returns (-1, "") if not found."""
    match = re.search(re.escape(anchor), haystack, re.IGNORECASE)
    if not match:
        return -1, ""
    return match.start(), match.group(0)


def insert_internal_links(
    article_markdown: str, candidates: list[dict], deepseek: DeepSeekClient,
) -> tuple[str, dict]:
    """candidates: [{"title", "wp_post_url"}, ...] from clients/db_client.py's
    get_published_articles(). Returns (possibly-updated markdown, report)."""
    report = {"links_inserted": 0, "links_skipped_no_match": 0, "links_skipped_bad_url": 0, "proposed": []}
    if not candidates:
        return article_markdown, report

    valid_urls = {c["wp_post_url"] for c in candidates if c.get("wp_post_url")}
    if not valid_urls:
        return article_markdown, report

    candidate_block = "\n".join(f"- {c['title']} -> {c['wp_post_url']}" for c in candidates if c.get("wp_post_url"))
    user_prompt = (
        f"Candidate articles (title -> URL) you may link to:\n{candidate_block}\n\n"
        f"--- ARTICLE ---\n{article_markdown}\n--- END ---"
    )
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, InternalLinkingResult, label="interlinking")
    proposed = result.model_dump()["links"]
    report["proposed"] = proposed

    updated = article_markdown
    inserted = 0
    for link in proposed:
        if inserted >= settings.interlinking_max_links:
            break
        if link["url"] not in valid_urls:
            report["links_skipped_bad_url"] += 1
            continue
        idx, matched_text = _find_anchor(updated, link["anchor_text"])
        if idx == -1:
            report["links_skipped_no_match"] += 1
            continue
        # Skip if this exact occurrence is already inside a markdown link -
        # a naive find() could otherwise double-wrap text a previous
        # iteration already linked.
        window_start = max(0, idx - 2)
        if updated[window_start:idx] == "](" or updated[idx - 1 : idx] == "[":
            report["links_skipped_no_match"] += 1
            continue
        # Skip if the match falls on a heading line (starts with "#") -
        # same bug as backlinking.py's <h1-6> guard: an anchor that happens
        # to be a full heading's text turns the whole heading into a link
        # instead of a natural in-sentence mention.
        line_start = updated.rfind("\n", 0, idx) + 1
        line_end = updated.find("\n", idx)
        line_end = len(updated) if line_end == -1 else line_end
        if updated[line_start:line_end].lstrip().startswith("#"):
            report["links_skipped_no_match"] += 1
            continue
        updated = updated[:idx] + f"[{matched_text}]({link['url']})" + updated[idx + len(matched_text):]
        inserted += 1

    report["links_inserted"] = inserted
    return updated, report
