"""Step 4 - structure & quality analysis.

Analyzes each competitor's full scraped content, heading structure,
metadata and table usage - not just "what type of content is this" but how
it's actually organized and how good it is. Falls back to title/snippet for
any competitor whose page didn't scrape.

Also judges directly_addresses_keyword per competitor - a page can rank for
the same general category (e.g. "backpacking 101") without actually
answering this specific keyword's request. That judgment gates whether a
competitor's stats count toward the hard structural targets in
article_strategy.py: measured directly, one run's SERP happened to surface
mostly generic "backpacking" content rather than gear-list-specific
articles, and a median computed across those gave a genuinely defensible-
looking but wrong target (1,394 words, driven by short generic pages that
were never trying to be a comprehensive gear list).

Full content, no excerpt truncation - see
pipeline/competitor_scraping.py's format_competitors_for_prompt.
"""

from clients.deepseek_client import DeepSeekClient
from models import CompetitorResearch
from pipeline.competitor_scraping import format_competitors_for_prompt

SYSTEM_PROMPT = """You are analyzing the structure and quality of what currently ranks for a \
target keyword, using the full page content, heading structure, metadata and table count fetched \
from each competitor - falling back to just the title and snippet for any competitor whose page \
couldn't be fetched.

For each competitor, in the SAME ORDER they were given: its content type (guide, listicle, \
comparison, review, product_page, forum_thread, or other); its angle; structure_notes describing \
concretely how it organizes itself (flat list of H2s vs. deep H2/H3 nesting, where tables appear \
and what they compare, how long each section runs); quality_notes assessing whether it's thin or \
thorough, current or stale, generic or specific with real detail; and \
directly_addresses_keyword - true only if this page is actually trying to answer THIS specific \
keyword's request (not just broadly in the same category). A "backpacking 101" overview page is \
NOT a direct match for "ultralight backpacking gear list" even though it ranks for a related \
query - it's answering a different, broader question. Be strict about TOPIC/CATEGORY mismatches \
like that.

Do NOT be strict about incidental qualifiers in the keyword - a platform/device (iPhone, Android), \
a location, or a year. A "Best Free Walking Apps" listicle still directly addresses "free walking \
apps for iphone" if the apps it covers are available on iPhone (most cross-platform apps are, \
unless the page says otherwise) - it doesn't need to say "iPhone" in the title or repeat the \
qualifier explicitly. Same logic for a year qualifier ("...2026") - a page from last year covering \
the same currently-available apps still counts. Only mark false on a qualifier mismatch when the \
page actively contradicts it (e.g. it's specifically an Android-only roundup, or reviews \
apps/services that have since shut down). False is the default only for genuine topic/category \
mismatches, not for a missing restatement of an obvious qualifier.

Separately, also be strict about SCOPE, not just topic - but judge scope at the level of the \
SPECIFIC PROCESS OR ENTITY the keyword is about, not the literal narrowest phrase. "Wild Casino \
withdrawal time" is fundamentally about ONE process: withdrawing money from Wild Casino \
specifically. A page dedicated to that process - its rules, fees, limits, requirements, waiting \
periods, or a specific withdrawal method - directly addresses the keyword, even if the page's own \
title emphasizes a different facet of that process (fees, limits, requirements) rather than the \
word "time" itself. Do NOT run the deletion test word-for-word against "time" alone - run it \
against the whole process the keyword names. "Withdrawal Limits And Fees - Wild Casino" and \
"Withdrawal Frequency Limits - Wild Casino" are official pages about that exact process and count \
as true, even though their titles say limits/fees/frequency, not time - a searcher asking about \
withdrawal time is well served by understanding limits and fees too, because they're the same \
process. This was measured to be a real failure mode: being too literal about the word "time" \
specifically caused every one of a casino's own official withdrawal pages to be marked false, \
leaving zero real competitors to compute a target from - worse than the over-broad problem this \
check was added to fix.

What DOES still fail the scope test: a page whose primary subject is a genuinely DIFFERENT, \
broader entity that happens to mention this process as one part among many - a full casino review \
covering games, bonuses, support, AND withdrawals (withdrawals is one section of a page about the \
whole casino, not a page about withdrawing) - or a roundup comparing MANY different casinos/brands \
where this one gets a paragraph. Also false for a genuinely different process at the same site (a \
"Deposit Rules" page is about depositing, not withdrawing - a different transaction direction) or \
a different brand entirely, even a similar-sounding one (a "Wildz Casino" or "Wild.io" page is NOT \
"Wild Casino" - check the actual brand name carefully, don't match on surface similarity).

Then summarize the pattern shared across most of the top results - the format AND structure that \
seems to be winning for this query, and why.

Return JSON matching the required schema only."""


def research_competitors(deepseek: DeepSeekClient, keyword: str, scraped_competitors: list[dict]) -> dict:
    user_prompt = f"Keyword: {keyword}\n\nCompetitors:\n{format_competitors_for_prompt(scraped_competitors)}"
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, CompetitorResearch, label="competitor_research")
    return result.model_dump()
