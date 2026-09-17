"""Retroactively links OLDER, already-live articles on the same site to a
BRAND-NEW one that just published - the reverse direction from
interlinking.py (new article -> old ones). Same LLM-proposes/code-applies
shape, but the target is live WordPress content (real HTML from a previous
publish), not local markdown, so a match gets wrapped as <a href>...</a>
rather than markdown link syntax.

There is no draft/review step anywhere in this pipeline (see
orchestrator.py) - a successful match here goes straight into a page real
visitors are already reading. The anchor text is still always a verbatim
find in that old post's own current content, and the url is always the
fixed URL of the article that just published - the LLM never gets to invent
either.
"""

import re

from clients import wordpress_client
from clients.deepseek_client import DeepSeekClient
from models import InternalLinkingResult

SYSTEM_PROMPT = """A brand-new article was just published on this website. You are choosing ONE \
place in an OLDER, already-live article where mentioning the new one would make sense to a \
reader - not rewriting anything, only picking a short phrase that ALREADY EXISTS verbatim in the \
older article's text below.

Rules:
- anchor_text must be copied EXACTLY from the OLDER ARTICLE'S TEXT BELOW - not from the new \
article's title, and not paraphrased or invented. The single most common mistake is picking the \
NEW article's own title as the anchor text instead of a phrase that actually appears in the older \
article below - do not do this. If you cannot find a real phrase in the older article that fits, \
return an empty list rather than guessing. Plain visible text only - never pick text that is \
inside an HTML tag or attribute.
- url must be exactly the one URL given for the new article.
- Only propose a link if the new article is genuinely, specifically relevant to that exact anchor \
phrase's topic - not just loosely related. A forced link is worse than no link. When in doubt, \
return an empty list. That is a correct answer, not a failure.
- Propose at most one link.

Return JSON matching the required schema only."""


def backlink_from_older_article(
    old_article_html: str, new_article_title: str, new_article_url: str, deepseek: DeepSeekClient,
) -> tuple[str, bool]:
    """Returns (possibly-updated HTML, whether a link was actually inserted)."""
    user_prompt = (
        f"New article just published: {new_article_title} -> {new_article_url}\n\n"
        f"--- OLDER ARTICLE (live HTML) ---\n{old_article_html}\n--- END ---"
    )
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, InternalLinkingResult, label="backlinking")
    proposed = result.model_dump()["links"]
    if not proposed or proposed[0]["url"] != new_article_url:
        return old_article_html, False

    match = re.search(re.escape(proposed[0]["anchor_text"]), old_article_html, re.IGNORECASE)
    if not match:
        return old_article_html, False
    idx, matched_text = match.start(), match.group(0)

    # A plain substring match on real HTML can land mid-tag or inside an
    # existing link's attributes - refuse rather than corrupt markup.
    preceding = old_article_html[:idx]
    if preceding.rfind("<") > preceding.rfind(">"):
        return old_article_html, False
    if re.search(r"<a\b[^>]*$", preceding):
        return old_article_html, False
    # Refuse anchors inside a heading - measured on a real run: the LLM
    # proposed an ENTIRE H2's text as the anchor, which is technically a
    # verbatim match but turns the whole heading into a link, not a natural
    # in-sentence mention. Checks whether the nearest heading tag before
    # this position hasn't been closed yet (i.e. we're still inside it).
    if re.search(r"<h[1-6]\b[^>]*>(?:(?!</h[1-6]>).)*$", preceding, re.IGNORECASE | re.DOTALL):
        return old_article_html, False

    updated = (
        old_article_html[:idx]
        + f'<a href="{new_article_url}">{matched_text}</a>'
        + old_article_html[idx + len(matched_text):]
    )
    return updated, True


def backlink_older_articles(
    new_article_title: str, new_article_url: str, candidates: list[dict], wp_config: dict,
    deepseek: DeepSeekClient, limit: int,
) -> dict:
    """candidates: published_articles rows for this website, EXCLUDING the
    one that just published (caller filters). Fetches each old post's live
    content fresh - it may have been hand-edited since we last wrote it -
    and updates it directly the moment a good anchor is found."""
    report = {"checked": 0, "linked": 0, "skipped": 0, "posts_linked": []}
    for candidate in candidates[:limit]:
        wp_post_id = candidate.get("wp_post_id")
        if not wp_post_id:
            continue
        report["checked"] += 1
        html = wordpress_client.get_post_content(wp_config, wp_post_id)
        if not html:
            report["skipped"] += 1
            continue
        updated_html, linked = backlink_from_older_article(html, new_article_title, new_article_url, deepseek)
        if not linked:
            report["skipped"] += 1
            continue
        if wordpress_client.update_post_content(wp_config, wp_post_id, updated_html):
            report["linked"] += 1
            report["posts_linked"].append(candidate.get("wp_post_url"))
        else:
            report["skipped"] += 1
    return report
