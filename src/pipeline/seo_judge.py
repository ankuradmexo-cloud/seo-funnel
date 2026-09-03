from src.clients.deepseek_client import DeepSeekClient
from src.models.schemas import DemandMetrics, SerpSignal, SeoJudgeOutput

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

Special case - coupon/promo-code/discount-code intent: these SERPs are typically dominated by \
huge deal-aggregator domains (RetailMeNot, Honey, Slickdeals, Coupons.com and similar) with \
massive domain authority and constantly-refreshed content - often a HARDER vertical for a small \
new site than ordinary review/comparison content, not easier, even when volume or difficulty \
numbers look inviting. If the top results are dominated by that kind of aggregator, reject or \
score low regardless of demand. The realistic opportunity in this space is narrow brand+category \
combinations specific enough that the big aggregators haven't bothered covering them - approve \
those when the SERP actually shows the gap, not just because the keyword sounds narrow.

Return JSON matching the required schema only."""


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
