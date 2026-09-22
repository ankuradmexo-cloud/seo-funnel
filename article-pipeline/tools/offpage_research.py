"""Off-page opportunity research across three channels - directories, guest
posts, and social/forum threads. Research + drafted outreach only: this
tool never submits a directory listing, never sends an email, and never
posts to Reddit or anywhere else. Every draft is meant to be reviewed and
sent/posted by a human, from their own account - see
migration_offpage_outreach.sql and the dashboard's Off-Page SEO page.

Usage:
    ./.venv/bin/python tools/offpage_research.py --channel directory --website-id 1
    ./.venv/bin/python tools/offpage_research.py --channel guest_post --website-id 1
    ./.venv/bin/python tools/offpage_research.py --channel social --website-id 1
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients import db_client
from clients.deepseek_client import DeepSeekClient
from clients.reddit_client import search_reddit
from clients.seranking_client import SERankingClient

MAX_QUERIES_PER_RUN = 4
MAX_RESULTS_PER_QUERY = 10


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return url


def _query_safe(category: str) -> str:
    """websites.category entries are natural-language prose ("business/SaaS
    software comparisons") and can carry a literal "/" or other punctuation
    that's harmless to a human reader but measured to get SE Ranking's SERP
    scraper permanently stuck in "processing" - the same failure mode as an
    unnatural/garbage test query (see clients/seranking_client.py). Strip it
    down to a plain space-separated phrase before it becomes part of a
    query."""
    cleaned = re.sub(r"[^\w\s]", " ", category)
    return re.sub(r"\s+", " ", cleaned).strip()


# --- directory ---------------------------------------------------------

class _DirectoryFilterResult(BaseModel):
    class Entry(BaseModel):
        url: str
        is_genuine_directory: bool
        listing_blurb: str  # empty string if is_genuine_directory is false

    entries: list[Entry]


_DIRECTORY_SYSTEM_PROMPT = """You are screening SERP results to find real business/niche \
directories - sites where a business can submit itself for a listing (review directories, \
citation sites, curated resource lists) - as opposed to unrelated content that merely ranks for \
a "submit your site"-style query.

For each URL given, with its title and snippet: set is_genuine_directory true only if it's \
plausibly a real directory/listing site accepting external submissions in this niche, false for \
anything else (a blog post about directories, an unrelated article, a competitor's own site).

For each genuine directory, write a listing_blurb: a short (1-2 sentence), factual description of \
OUR site suitable for submitting as a directory listing - using the site name and category given. \
Leave listing_blurb as an empty string for anything not genuine.

Return JSON matching the required schema only, same order as given."""


def research_directories(website_id: int, seranking: SERankingClient, deepseek: DeepSeekClient) -> list[dict]:
    wp_config = db_client.get_website_wp_config(website_id) or {}
    site_name = wp_config.get("name") or f"website {website_id}"
    categories = db_client.get_website_categories(website_id)
    if not categories:
        return []

    queries = [f"{_query_safe(c)} submit your site directory" for c in categories[:MAX_QUERIES_PER_RUN]]
    seen_domains: set[str] = set()
    results: list[dict] = []
    for q in queries:
        serp = seranking.serp_search(q)
        for r in serp["top_results"][:MAX_RESULTS_PER_QUERY]:
            domain = _domain(r.get("link") or "")
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            results.append(r)

    if not results:
        return []

    listing = "\n".join(
        f"{i}. {r.get('link')}\n   title: {r.get('title')}\n   snippet: {r.get('snippet') or '(none)'}"
        for i, r in enumerate(results)
    )
    user_prompt = (
        f"Our site: {site_name}, categories: {', '.join(categories)}\n\nCandidate URLs:\n{listing}"
    )
    filtered = deepseek.structured_call(
        _DIRECTORY_SYSTEM_PROMPT, user_prompt, _DirectoryFilterResult, label="offpage_directory_filter",
    ).model_dump()

    opportunities = []
    for entry in filtered["entries"]:
        if not entry["is_genuine_directory"]:
            continue
        opportunities.append({
            "target_url": entry["url"],
            "target_domain": _domain(entry["url"]),
            "title": next((r.get("title") for r in results if r.get("link") == entry["url"]), None),
            "signal_summary": "Directory accepting submissions in a matching category",
            "contact_info": entry["url"],
            "outreach_draft": entry["listing_blurb"],
        })
    return opportunities


# --- guest post ----------------------------------------------------------

class _GuestPostFilterResult(BaseModel):
    class Entry(BaseModel):
        url: str
        is_genuine_guest_post_page: bool
        pitch_subject: str
        pitch_body: str  # empty strings if not genuine

    entries: list[Entry]


_GUEST_POST_SYSTEM_PROMPT = """You are screening SERP results to find real "write for us"/guest \
contributor pages - as opposed to unrelated content that merely ranks for a guest-post query.

For each URL given, with its title and snippet: set is_genuine_guest_post_page true only if it \
plausibly IS a page accepting external guest contributions in this niche, false otherwise.

For each genuine one, draft a short (under 120 words) pitch email: pitch_subject and pitch_body. \
Reference our real published article (title/URL given) as proof of the kind of content we'd \
contribute - specific, not generic ("I write about X and would love to contribute" is too vague; \
name the actual angle our article took). No flattery filler, no exclamation points, sound like a \
real person who read their guidelines. Leave pitch_subject/pitch_body as empty strings for \
anything not genuine.

Return JSON matching the required schema only, same order as given."""


def research_guest_posts(website_id: int, seranking: SERankingClient, deepseek: DeepSeekClient) -> list[dict]:
    wp_config = db_client.get_website_wp_config(website_id) or {}
    site_name = wp_config.get("name") or f"website {website_id}"
    categories = db_client.get_website_categories(website_id)
    published = db_client.get_published_articles(website_id, limit=1)
    if not categories or not published:
        return []
    our_article = published[0]

    queries = [f"{_query_safe(c)} write for us" for c in categories[:MAX_QUERIES_PER_RUN]]
    seen_domains: set[str] = set()
    results: list[dict] = []
    for q in queries:
        serp = seranking.serp_search(q)
        for r in serp["top_results"][:MAX_RESULTS_PER_QUERY]:
            domain = _domain(r.get("link") or "")
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            results.append(r)

    if not results:
        return []

    listing = "\n".join(
        f"{i}. {r.get('link')}\n   title: {r.get('title')}\n   snippet: {r.get('snippet') or '(none)'}"
        for i, r in enumerate(results)
    )
    user_prompt = (
        f"Our site: {site_name}, categories: {', '.join(categories)}\n"
        f"Our article to reference: \"{our_article['title']}\" - {our_article['wp_post_url']}\n\n"
        f"Candidate URLs:\n{listing}"
    )
    filtered = deepseek.structured_call(
        _GUEST_POST_SYSTEM_PROMPT, user_prompt, _GuestPostFilterResult, label="offpage_guest_post_filter",
    ).model_dump()

    opportunities = []
    for entry in filtered["entries"]:
        if not entry["is_genuine_guest_post_page"]:
            continue
        draft = f"Subject: {entry['pitch_subject']}\n\n{entry['pitch_body']}"
        opportunities.append({
            "target_url": entry["url"],
            "target_domain": _domain(entry["url"]),
            "title": next((r.get("title") for r in results if r.get("link") == entry["url"]), None),
            "signal_summary": "Accepts external guest contributions in a matching category",
            "contact_info": entry["url"],
            "outreach_draft": draft,
        })
    return opportunities


# --- social / forum -------------------------------------------------------

class _SocialFilterResult(BaseModel):
    class Entry(BaseModel):
        permalink: str
        is_genuine_fit: bool
        reply_draft: str  # empty string if not a genuine fit

    entries: list[Entry]


_SOCIAL_SYSTEM_PROMPT = """You are screening Reddit threads to find ones where our real published \
article would be a genuinely helpful, on-topic answer - not an excuse to drop a link.

For each thread given (title, subreddit, snippet): set is_genuine_fit true ONLY if someone asking \
this exact question would be well served by our article's actual content - reject anything where \
the connection is a stretch, off-topic, or where dropping a link would read as spam.

For each genuine fit, draft a short, helpful reply (under 100 words) that actually answers the \
question in its own right - genuinely useful even if the reader never clicks the link - and \
mentions our article naturally, once, only if it adds real value. No "check out my site" energy, \
no exclamation points. Leave reply_draft as an empty string for anything not a genuine fit.

Return JSON matching the required schema only, same order as given. This is a DRAFT ONLY, never \
posted automatically - written for a human to review and post themselves."""


def research_social(website_id: int, deepseek: DeepSeekClient) -> list[dict]:
    categories = db_client.get_website_categories(website_id)
    published = db_client.get_published_articles(website_id, limit=5)
    if not categories or not published:
        return []

    threads: list[dict] = []
    seen_links: set[str] = set()
    for c in categories[:MAX_QUERIES_PER_RUN]:
        for t in search_reddit(c, limit=MAX_RESULTS_PER_QUERY):
            if not t.get("permalink") or t["permalink"] in seen_links:
                continue
            seen_links.add(t["permalink"])
            threads.append(t)

    if not threads:
        return []

    articles_block = "\n".join(f"- \"{a['title']}\" - {a['wp_post_url']}" for a in published)
    threads_block = "\n".join(
        f"{i}. [{t.get('subreddit')}] {t['title']}\n   {t.get('permalink')}\n   {t.get('selftext') or '(no body text)'}"
        for i, t in enumerate(threads)
    )
    user_prompt = f"Our published articles:\n{articles_block}\n\nCandidate threads:\n{threads_block}"
    filtered = deepseek.structured_call(
        _SOCIAL_SYSTEM_PROMPT, user_prompt, _SocialFilterResult, label="offpage_social_filter",
    ).model_dump()

    opportunities = []
    for entry in filtered["entries"]:
        if not entry["is_genuine_fit"]:
            continue
        thread = next((t for t in threads if t.get("permalink") == entry["permalink"]), None)
        if thread is None:
            continue
        opportunities.append({
            "target_url": thread["permalink"],
            "target_domain": "reddit.com",
            "title": thread["title"],
            "signal_summary": f"r/{thread.get('subreddit')} - {thread.get('num_comments', 0)} comments, score {thread.get('score', 0)}",
            "contact_info": None,
            "outreach_draft": entry["reply_draft"],
        })
    return opportunities


CHANNELS = {"directory": research_directories, "guest_post": research_guest_posts, "social": research_social}


def main():
    parser = argparse.ArgumentParser(description="Off-page opportunity research for one website.")
    parser.add_argument("--channel", required=True, choices=list(CHANNELS))
    parser.add_argument("--website-id", type=int, required=True)
    parser.add_argument("--job-id", type=int, default=None,
                         help="outreach_jobs.job_id (set by the dashboard trigger) - when given, "
                              "results are written to Supabase and the job is marked finished.")
    args = parser.parse_args()

    deepseek = DeepSeekClient()

    try:
        if args.channel == "social":
            opportunities = research_social(args.website_id, deepseek)
        else:
            seranking = SERankingClient()
            opportunities = CHANNELS[args.channel](args.website_id, seranking, deepseek)
    except Exception as e:
        if args.job_id:
            db_client.finish_offpage_job(args.job_id, "failed", str(e))
        raise

    print(f"\n{len(opportunities)} {args.channel} opportunity(ies) found:\n")
    for o in opportunities:
        print(f"  {o['target_domain']}: {o['title']}")
        print(f"    {o['target_url']}")

    if args.job_id:
        db_client.record_offpage_opportunities(args.job_id, args.website_id, args.channel, opportunities)
        db_client.finish_offpage_job(args.job_id, "success")
        print(f"\nWrote {len(opportunities)} opportunity(ies) to Supabase for job {args.job_id}.")


if __name__ == "__main__":
    main()
