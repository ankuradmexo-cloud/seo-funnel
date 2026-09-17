"""Competitor backlink gap analysis - research only, no outreach happens
here or anywhere in this pipeline.

For a keyword: finds who's currently ranking, pulls each competing page's
backlinks, and surfaces domains that link to MULTIPLE competitors - the
core "link gap" technique. A domain that already links to 2+ pages
covering this exact topic has proven, repeatedly, that it's willing to
link to content like this - a much higher-probability outreach target
than a random relevant-seeming site.

Cost: SE Ranking's backlinks/all is 1 credit per backlink row, not the
flat-rate keywords/export this pipeline uses elsewhere - top_n competitors
x per-competitor limit credits per run (default 5 x 50 = 250 credits).

Usage:
    ./.venv/bin/python tools/backlink_gap.py "crm software examples"
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients import db_client
from clients.backlink_client import get_backlinks
from clients.deepseek_client import DeepSeekClient
from clients.scrappa_client import ScrappaClient
from pipeline.serp_research import research_serp

MIN_COMPETITORS_LINKED = 2  # the actual "gap" threshold - below this, it's just one page's backlink list

# Quality bar, added after a real test run ("crm software examples") returned
# 3 "gap" domains that were all noise on inspection: a search-aggregator
# portal page and two thin/near-zero-authority sites. Without a floor, this
# tool just reports overlap, not "worth pursuing" - these two checks are
# what actually separates a real outreach candidate from noise.
MIN_DOMAIN_RANK = 30
_AGGREGATOR_MARKERS = ("?search=", "&search=", "/search?", "?q=", "&q=")


def _looks_like_aggregator(url: str) -> bool:
    """Search-results/portal pages (e.g. a site's internal search results
    for a query) aren't editorial content - a link FROM one isn't a real
    endorsement, just an auto-generated results page."""
    return any(marker in url for marker in _AGGREGATOR_MARKERS)


# A real "live casino app" test run surfaced dozens of domains whose anchor
# text literally advertised a black-hat backlink service ("SEO BACKLINKS,
# CROSS-LINKS, HACKED WP-ADMIN - TELEGRAM @SEO_ANOMALY") on sites with no
# topical reason to link to a casino app (a Japanese food blog, a Romanian
# student business site) - compromised WordPress installs being used to
# inject spam links, not real endorsements. Domain-rank alone doesn't catch
# this (several scored 40-73), so the anchor text itself is the signal.
_SPAM_ANCHOR_MARKERS = ("seo_anomaly", "seo backlinks", "cross-links", "hacked wp", "telegram")


def _looks_like_spam_anchor(anchor: str) -> bool:
    anchor_lower = (anchor or "").lower()
    return any(marker in anchor_lower for marker in _SPAM_ANCHOR_MARKERS)


# A "wave quickbooks alternative" test run showed a SUBTLER problem the two
# filters above can't catch: 52 domains with natural-sounding anchor text,
# real domain-rank scores, and no spam markers at all - but the referring
# SITES themselves were a fashion blog, a celebrity-gossip site, an
# upcycling blog, a real-estate network - all independently "discovering"
# the same accounting-software angle and linking to the same single URL.
# That's the signature of a paid link/PBN campaign, just one that avoids
# spam-looking anchor text. Rank and anchor-text checks can't catch it -
# only "is the REFERRING site itself topically relevant" can, which needs
# real judgment, not a keyword match.
class _RelevanceCheck(BaseModel):
    relevant_domains: list[str]


_RELEVANCE_SYSTEM_PROMPT = """You are screening candidate backlink-outreach targets for a niche keyword. \
For each domain below, you're given its own referring page title(s) - judge whether that domain's OWN \
content is genuinely, topically relevant to the niche behind the keyword, not just a generic \
small-business/lifestyle blog that happens to have one tangential post mentioning it.

The specific pattern to reject: a large paid-link or PBN network often shows many completely \
unrelated-niche sites (fashion, beauty, celebrity gossip, real estate, upcycling, etc.) all \
independently publishing near-identical filler content that links to the same target - that is NOT \
organic editorial interest, exclude those domains even if their anchor text and page title look \
superficially on-topic.

Keep only domains whose own apparent focus/niche (based on the page titles given) is genuinely aligned \
with the keyword's subject matter - a real industry blog, a real product-review site, a real trade \
publication, etc.

Return JSON matching the required schema only - relevant_domains is the list of domains (exact strings \
as given) that pass this bar. An empty list is a correct answer if none genuinely fit."""


def _filter_by_topical_relevance(deepseek: DeepSeekClient, gap: list[dict], keyword: str) -> list[dict]:
    if not gap:
        return gap
    domain_block = "\n".join(
        f"- {row['referring_domain']}: "
        + "; ".join((s.get("from_page_title") or s["from_page"]) for s in row["sample_links"])
        for row in gap
    )
    user_prompt = f"Keyword/niche: {keyword}\n\nCandidate domains and their referring page titles:\n{domain_block}"
    result = deepseek.structured_call(
        _RELEVANCE_SYSTEM_PROMPT, user_prompt, _RelevanceCheck, label="backlink_relevance_filter",
    )
    relevant = {d.lower() for d in result.model_dump()["relevant_domains"]}
    return [row for row in gap if row["referring_domain"].lower() in relevant]


def find_link_gap(
    keyword: str, top_n: int = 5, per_competitor_limit: int = 50,
    min_competitors_linked: int = MIN_COMPETITORS_LINKED,
) -> dict:
    scrappa = ScrappaClient()
    serp = research_serp(scrappa, keyword, top_n)
    competing_urls = [r["link"] for r in serp["top_results"][:top_n] if r.get("link")]

    # referring_domain -> {"competitors_linked": set(url_to), "domain_inlink_rank": int, "sample_links": [...]}
    domains: dict[str, dict] = defaultdict(lambda: {"competitors_linked": set(), "domain_inlink_rank": 0, "sample_links": []})

    for url in competing_urls:
        try:
            backlinks = get_backlinks(url, mode="url", limit=per_competitor_limit)
        except Exception as e:  # noqa: BLE001 - one competitor's failed lookup shouldn't kill the whole report
            print(f"  warning: backlink lookup failed for {url}: {e}", file=sys.stderr)
            continue
        for bl in backlinks:
            if (
                _looks_like_aggregator(bl["url_from"])
                or _looks_like_spam_anchor(bl.get("anchor"))
                or bl.get("domain_inlink_rank", 0) < MIN_DOMAIN_RANK
            ):
                continue
            from_domain = bl["url_from"].split("/")[2] if "://" in bl["url_from"] else bl["url_from"]
            entry = domains[from_domain]
            entry["competitors_linked"].add(url)
            entry["domain_inlink_rank"] = max(entry["domain_inlink_rank"], bl.get("domain_inlink_rank", 0))
            entry["sample_links"].append({
                "linked_to_competitor": url,
                "from_page": bl["url_from"],
                "from_page_title": bl.get("title"),
                "anchor": bl.get("anchor"),
                "nofollow": bl.get("nofollow"),
            })

    gap = [
        {
            "referring_domain": domain,
            "competitors_linked_count": len(data["competitors_linked"]),
            "domain_inlink_rank": data["domain_inlink_rank"],
            "sample_links": data["sample_links"][:3],
        }
        for domain, data in domains.items()
        if len(data["competitors_linked"]) >= min_competitors_linked
    ]
    gap.sort(key=lambda d: (-d["competitors_linked_count"], -d["domain_inlink_rank"]))

    pre_relevance_count = len(gap)
    if gap:
        deepseek = DeepSeekClient()
        gap = _filter_by_topical_relevance(deepseek, gap, keyword)

    return {
        "keyword": keyword,
        "competing_urls_checked": competing_urls,
        "gap_domains_before_relevance_filter": pre_relevance_count,
        "gap_domains_found": len(gap),
        "gap": gap,
    }


def main():
    parser = argparse.ArgumentParser(description="Competitor backlink gap analysis for one keyword.")
    parser.add_argument("keyword")
    parser.add_argument("--top-n", type=int, default=5, help="How many competing pages to check.")
    parser.add_argument("--limit", type=int, default=50, help="Max backlinks pulled per competitor (SE Ranking credits).")
    parser.add_argument("--min-competitors", type=int, default=MIN_COMPETITORS_LINKED,
                         help="Minimum competitors a domain must link to, to count as a 'gap' - set 1 to see any quality backlink, not just proven repeat-linkers.")
    parser.add_argument("--job-id", type=int, default=None,
                         help="backlink_gap_jobs.job_id (set by the dashboard trigger) - when given, results are written to Supabase and the job is marked finished, in addition to the local JSON report.")
    parser.add_argument("--website-id", type=int, default=None, help="Required with --job-id.")
    parser.add_argument("--keyword-id", type=int, default=None, help="Required with --job-id.")
    args = parser.parse_args()

    print(f"Checking top {args.top_n} competitors for {args.keyword!r} (up to {args.top_n * args.limit} SE Ranking credits)...")

    try:
        result = find_link_gap(args.keyword, args.top_n, args.limit, args.min_competitors)
    except Exception as e:
        if args.job_id:
            db_client.finish_backlink_gap_job(args.job_id, "failed", str(e))
        raise

    print(f"\n{result['gap_domains_found']} domain(s) link to {args.min_competitors}+ competing page(s):\n")
    for row in result["gap"]:
        print(f"  {row['referring_domain']} (rank {row['domain_inlink_rank']}, links to {row['competitors_linked_count']} competitors)")
        for s in row["sample_links"]:
            print(f"      from {s['from_page']}  ->  {s['linked_to_competitor']}  (anchor: {s['anchor']!r}, nofollow: {s['nofollow']})")

    out_path = Path(__file__).parent.parent / "output" / f"backlink_gap_{args.keyword.replace(' ', '-')}.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"\nFull report: {out_path}")

    if args.job_id:
        db_client.record_backlink_candidates(args.job_id, args.keyword_id, args.website_id, result["gap"])
        db_client.finish_backlink_gap_job(args.job_id, "success")
        print(f"Wrote {len(result['gap'])} candidate(s) to Supabase for job {args.job_id}.")


if __name__ == "__main__":
    main()
