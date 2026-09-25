from src.clients.deepseek_client import DeepSeekClient
from src.models.schemas import BatchSeoJudgeOutput, DemandMetrics, SerpSignal, SeoJudgeOutput

SYSTEM_PROMPT = """You are an SEO opportunity judge for a small, low-authority website. \
Score a keyword candidate 0-100 on whether it is a realistic, winnable ranking opportunity. \
You'll be given a keyword difficulty score (0-100, organic ranking difficulty) and the actual \
top organic results for this keyword - use the results to judge whether the ranking pages look \
like they're from large/authoritative sites (hard to unseat) or weaker, thinner, less-targeted \
content a small site could realistically outrank, and use the difficulty score as a cross-check \
on that read rather than trusting either signal alone. Weigh that alongside search demand, paid \
competition, uniqueness vs. what the site already targets, and the two real signals below.

Search intent codes (from SE Ranking, not inferred): I=informational, C=commercial, \
T=transactional, L=local, N=navigational. Treat EVERY intent type as an equally valid target. \
Informational keywords are legitimate, often excellent opportunities for a content site - do NOT \
score a keyword lower because it is informational, or because it lacks commercial intent. Use \
the intent codes only to understand what kind of page would serve the query, never as a reason \
to reject or discount it.

12-month volume trend: use this to distinguish a keyword that's been stable, one that's \
genuinely trending up (real emerging opportunity, worth some extra optimism even if current \
volume is modest), and one that's declining (don't chase a keyword on its way out based on a \
now-stale average volume number).

Approve only genuinely winnable opportunities, not just high-volume terms.

Before approving anything, apply this general test regardless of niche: does actually satisfying \
this searcher require real explanation, comparison, or synthesis across multiple points - or does \
the honest, complete answer fit in a sentence or two that a reader scans and then leaves? If it's \
the latter, no length or quality of writing changes that - reject regardless of how good the \
volume or difficulty numbers look. This is the same underlying test behind both special cases \
below; they're the two most common shapes it takes, not the only ones. A keyword can fail this \
test in a shape neither special case names - judge the actual query, not just whether it matches \
one of the two patterns below.

Special case - coupon/promo-code/discount-code intent: these SERPs are typically dominated by \
huge deal-aggregator domains (RetailMeNot, Honey, Slickdeals, Coupons.com and similar) with \
massive domain authority and constantly-refreshed content - often a HARDER vertical for a small \
new site than ordinary review/comparison content, not easier, even when volume or difficulty \
numbers look inviting. If the top results are dominated by that kind of aggregator, reject or \
score low regardless of demand. The realistic opportunity in this space is narrow brand+category \
combinations specific enough that the big aggregators haven't bothered covering them - approve \
those when the SERP actually shows the gap, not just because the keyword sounds narrow.

Special case - local intent (a "... near me" / "... nearby" / "... in [city]" pattern in the \
keyword itself, or the SERP actually showing it): these queries are typically won by Google's \
Local Pack (the map plus three business listings shown above organic results) or by \
store-locator and directory pages, not by written content - a real searcher wants a place on a \
map, not an article to read. No amount of content quality can outrank a map. Judge this from the \
keyword's own wording and the actual top organic results - store locators, chain "find a \
location" pages, or directory listings (Yelp, Google Maps, similar) actually ranking - not from \
SE Ranking's "L" intent code alone. That code is measured to be noisy: it has been seen on \
ordinary national product/category keywords ("plus size jackets", "used printer sale") that carry \
no real local signal in either the wording or the SERP, and rejecting on the code alone would \
throw those out along with the genuine local ones. If the wording or the actual SERP shows real \
local intent, reject regardless of demand or difficulty, full stop - a low difficulty score here \
usually means low ARTICLE competition, not a real opening, because articles are not what is \
actually competing for this query. If neither the wording nor the SERP shows it, the "L" code \
alone is not sufficient reason to reject.

Special case - quick-fact / price-lookup intent: some queries are satisfied by a single current \
fact - a price, a menu item, a deal amount, a phone number, store hours - not by explanation, \
comparison, or depth. The searcher scans for that one answer and leaves; they do not read a \
1,500+ word article start to finish, no matter how well it's written. Common patterns: "[brand] \
menu prices", "[brand] deals", "[brand] family meal", "[brand] hours", "[brand] phone number", or \
any "what does X currently cost/include at [specific business]" query tied to one place. These \
can carry inviting-looking volume and difficulty numbers while still being a bad fit - the content \
gets written, technically ranks, and still doesn't get read, because it's answering a lookup with \
an essay. Judge from what's actually ranking, not just the keyword's surface pattern: if the real \
top results are the business's own official pricing/menu page, a quick-answer aggregator, or a \
thin fact-listing page, reject - that's the format actually winning, and no article beats a page \
that just states the number faster. If instead genuine articles with real explanatory or \
comparative depth (reviews, buying guides, "X vs Y", "is it worth it") are what's actually ranking, \
that's evidence readers DO engage with longer content here - approve it on that basis.

Return JSON matching the required schema only."""

_BATCH_WRAPPER = """You will be given MULTIPLE keywords to judge in this one call, each under its \
own "### Keyword: <text>" heading with its own data. Apply every rule above to EACH keyword \
independently - one keyword's demand/SERP/niche fit must never influence another's verdict. \
Return one verdict per keyword given, no more, no fewer, each with its `keyword` field set to \
that keyword's exact text (verbatim, used to match verdicts back to candidates - do not \
paraphrase or normalize it). Return JSON matching the required schema only."""

BATCH_SYSTEM_PROMPT = SYSTEM_PROMPT.rsplit("\n\nReturn JSON matching the required schema only.", 1)[0] + "\n\n" + _BATCH_WRAPPER


def _demand_block(keyword: str, demand: DemandMetrics, serp: SerpSignal) -> str:
    results_block = "\n".join(
        f"  {r.position}. {r.title} ({r.source}) - {r.snippet}"
        for r in serp.top_results
    ) or "  (no organic results returned)"
    trend_block = ", ".join(
        f"{month}:{vol}" for month, vol in sorted(demand.history_trend.items())
    ) or "(no trend data)"
    return (
        f"Search volume: {demand.search_volume}\n"
        f"CPC: {demand.cpc}\n"
        f"Paid competition (0-1): {demand.competition}\n"
        f"Keyword difficulty (0-100): {demand.difficulty}\n"
        f"Search intent codes: {', '.join(demand.intents) or '(none returned)'}\n"
        f"12-month volume trend: {trend_block}\n"
        f"Total Google results for this query: {serp.total_results}\n"
        f"Current top organic results:\n{results_block}\n"
    )


def judge_keywords_batch(
    deepseek: DeepSeekClient,
    candidates: list[tuple[str, DemandMetrics, SerpSignal]],
    niche: str,
) -> list[SeoJudgeOutput]:
    """Judges multiple keywords in ONE DeepSeek call instead of one call
    per keyword - the fixed system prompt (the bulk of a judge call's
    tokens, ~1,850 of ~2,000 measured on a real call) is paid for once per
    batch instead of once per keyword, meaningfully cutting spend once
    every demand-validated candidate gets judged instead of stopping at
    the daily shortlist target (see orchestrator.py). Falls back to
    per-keyword judging for any keyword the batch response is missing a
    verdict for, rather than silently dropping it."""
    if not candidates:
        return []

    blocks = "\n".join(
        f"### Keyword: {kw}\nNiche: {niche}\n{_demand_block(kw, demand, serp)}"
        for kw, demand, serp in candidates
    )
    user_prompt = f"Judge each of the following {len(candidates)} keywords independently:\n\n{blocks}"
    result = deepseek.structured_call(BATCH_SYSTEM_PROMPT, user_prompt, BatchSeoJudgeOutput)
    verdicts_by_keyword = {v.keyword: v for v in result.verdicts}

    out = []
    for kw, demand, serp in candidates:
        verdict = verdicts_by_keyword.get(kw)
        if verdict is None:
            # Batch response dropped this one - judge it alone rather than
            # silently losing a candidate that already cost real SE Ranking/
            # Scrappa credits to reach this point.
            verdict = judge_keyword(deepseek, kw, demand, serp, niche)
        out.append(verdict)
    return out


def judge_keyword(
    deepseek: DeepSeekClient,
    keyword: str,
    demand: DemandMetrics,
    serp: SerpSignal,
    niche: str,
) -> SeoJudgeOutput:
    results_block = "\n".join(
        f"  {r.position}. {r.title} ({r.source}) - {r.snippet}"
        for r in serp.top_results
    ) or "  (no organic results returned)"

    trend_block = ", ".join(
        f"{month}:{vol}" for month, vol in sorted(demand.history_trend.items())
    ) or "(no trend data)"

    user_prompt = (
        f"Keyword: {keyword}\n"
        f"Niche: {niche}\n"
        f"Search volume: {demand.search_volume}\n"
        f"CPC: {demand.cpc}\n"
        f"Paid competition (0-1): {demand.competition}\n"
        f"Keyword difficulty (0-100): {demand.difficulty}\n"
        f"Search intent codes: {', '.join(demand.intents) or '(none returned)'}\n"
        f"12-month volume trend: {trend_block}\n"
        f"Total Google results for this query: {serp.total_results}\n"
        f"Current top organic results:\n{results_block}\n"
    )
    return deepseek.structured_call(SYSTEM_PROMPT, user_prompt, SeoJudgeOutput)
