"""Off-page opportunity research across three channels - directories,
resource-page link building, and broken-link building. (A fourth,
social/forum, exists in code below but isn't exposed in the dashboard -
Reddit's Data Access Request for this app was rejected, and there's no
other real data source, so it has nothing to search with right now.)
Guest posts were removed as a channel entirely - too high-effort for this
team to act on (writing a full article per opportunity), unlike the other
three which are a one-line pitch email or a form submission.

Research + drafted outreach only: this tool never submits a directory
listing, never sends an email, and never posts to Reddit or anywhere else.
Every draft is meant to be reviewed and sent/posted by a human, from their
own account - see migration_offpage_outreach.sql and the dashboard's
Off-Page SEO page.

Usage:
    ./.venv/bin/python tools/offpage_research.py --channel directory --website-id 1
    ./.venv/bin/python tools/offpage_research.py --channel resource_page --website-id 1
    ./.venv/bin/python tools/offpage_research.py --channel broken_link --website-id 1
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients import db_client
from clients.deepseek_client import DeepSeekClient
from clients.page_scraper import USER_AGENT
from clients.reddit_client import search_reddit
from clients.seranking_client import SERankingClient
from config import settings

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


_DIRECTORY_SYSTEM_PROMPT = """You are screening SERP results to find real directories our site can \
submit ITSELF to for a listing - as opposed to unrelated content, or a directory meant for a \
different KIND of submission than what we are.

Our site is a content/media/review website - it publishes articles, guides, and reviews. It is \
NOT a SaaS product, an app, a tool, or a business with a physical location. This distinction is \
the main thing to filter on: many "submit your AI tool" / "submit your app" style directories \
(startup/tool directories like a Product Hunt-style listing, an app store, a SaaS directory) \
exist for people submitting an actual PRODUCT to be listed and reviewed - our site would not \
qualify there even if the category name matches, because we're the one writing reviews, not a \
product to be reviewed. Set is_genuine_directory false for any directory that expects a product/\
tool/app/business submission rather than a website/blog/publication submission.

Genuine directories for our kind of site look like: web directories, blog directories, content/\
media directories, review-site directories, niche resource/link lists that accept a website URL \
plus a description - the kind of listing a publication or blog would submit itself to, not a \
product would.

For each URL given, with its title and snippet: set is_genuine_directory true only if it's \
plausibly a real directory/listing site that would accept a CONTENT WEBSITE like ours, false for \
anything else (a blog post about directories, an unrelated article, a competitor's own site, or a \
directory meant for products/apps/businesses rather than websites).

For each genuine directory, write a listing_blurb: a short (1-2 sentence), factual description of \
OUR site suitable for submitting as a directory listing - using the site name and category given, \
and describing it as the review/content website it is. Leave listing_blurb as an empty string for \
anything not genuine.

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
        f"Our site: {site_name}, a content/review website covering: {', '.join(categories)}\n\n"
        f"Candidate URLs:\n{listing}"
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


def _find_resource_pages(website_id: int, seranking: SERankingClient, query_suffix: str) -> tuple[list[str], list[dict]]:
    """Shared discovery step for resource_page and broken_link - both are
    looking for the same kind of page (a curated "resources"/"useful
    links" list in the site's niche), just doing something different with
    it once found. Returns (categories, raw SERP results)."""
    categories = db_client.get_website_categories(website_id)
    if not categories:
        return [], []

    queries = [f"{_query_safe(c)} {query_suffix}" for c in categories[:MAX_QUERIES_PER_RUN]]
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
    return categories, results


# --- resource page link building ------------------------------------------

class _ResourcePageFilterResult(BaseModel):
    class Entry(BaseModel):
        url: str
        is_genuine_resource_page: bool
        pitch_subject: str
        pitch_body: str  # empty strings if not genuine

    entries: list[Entry]


_RESOURCE_PAGE_SYSTEM_PROMPT = """You are screening SERP results to find real curated resource/\
link-list pages - a page whose whole purpose is linking out to other useful pages in a niche (a \
"best resources for X" page, a "useful X links" page, a curated linkroll) - as opposed to an \
ordinary article, a directory homepage, or a competitor's own content.

For each URL given, with its title and snippet: set is_genuine_resource_page true only if it's \
plausibly a page that curates/links to OTHER sites' pages as its main content, false otherwise \
(an ordinary blog post, a product page, a "write for us" page, an unrelated result).

For each genuine one, draft a short (under 100 words) pitch email: pitch_subject and pitch_body, \
asking to be considered for addition to that specific list. Reference our real published article \
(title/URL given) and say concretely why it fits that page's existing list, not generically. No \
flattery filler, no exclamation points. Leave pitch_subject/pitch_body as empty strings for \
anything not genuine.

Return JSON matching the required schema only, same order as given."""


def research_resource_pages(website_id: int, seranking: SERankingClient, deepseek: DeepSeekClient) -> list[dict]:
    wp_config = db_client.get_website_wp_config(website_id) or {}
    site_name = wp_config.get("name") or f"website {website_id}"
    published = db_client.get_published_articles(website_id, limit=1)
    if not published:
        return []
    our_article = published[0]

    categories, results = _find_resource_pages(website_id, seranking, "resources list")
    if not results:
        return []

    listing = "\n".join(
        f"{i}. {r.get('link')}\n   title: {r.get('title')}\n   snippet: {r.get('snippet') or '(none)'}"
        for i, r in enumerate(results)
    )
    user_prompt = (
        f"Our site: {site_name}, categories: {', '.join(categories)}\n"
        f"Our article to pitch: \"{our_article['title']}\" - {our_article['wp_post_url']}\n\n"
        f"Candidate URLs:\n{listing}"
    )
    filtered = deepseek.structured_call(
        _RESOURCE_PAGE_SYSTEM_PROMPT, user_prompt, _ResourcePageFilterResult, label="offpage_resource_page_filter",
    ).model_dump()

    opportunities = []
    for entry in filtered["entries"]:
        if not entry["is_genuine_resource_page"]:
            continue
        draft = f"Subject: {entry['pitch_subject']}\n\n{entry['pitch_body']}"
        opportunities.append({
            "target_url": entry["url"],
            "target_domain": _domain(entry["url"]),
            "title": next((r.get("title") for r in results if r.get("link") == entry["url"]), None),
            "signal_summary": "Curated resource/link-list page in a matching category",
            "contact_info": entry["url"],
            "outreach_draft": draft,
        })
    return opportunities


# --- broken link building ---------------------------------------------------

MAX_RESOURCE_PAGES_TO_CRAWL = 4
MAX_OUTBOUND_LINKS_CHECKED = 15
LINK_CHECK_TIMEOUT = 5.0


def _find_broken_links(page_url: str) -> list[dict]:
    """Fetches one resource page, extracts its outbound links (external
    domains only - a page's own internal navigation isn't what we're
    checking), and HEAD-checks a bounded sample for dead ones. Best-effort:
    a page that can't be fetched, or a link that can't be conclusively
    checked (timeout, blocks HEAD requests), is just skipped rather than
    treated as broken - false positives would make the drafted pitch
    factually wrong ("your link is dead" when it isn't)."""
    try:
        resp = httpx.get(page_url, headers={"User-Agent": USER_AGENT}, timeout=LINK_CHECK_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    page_domain = _domain(page_url)
    candidates: list[tuple[str, str]] = []  # (absolute_url, anchor_text)
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if not href.startswith("http") or _domain(href) == page_domain or href in seen:
            continue
        seen.add(href)
        candidates.append((href, a.get_text(strip=True)[:100]))
        if len(candidates) >= MAX_OUTBOUND_LINKS_CHECKED:
            break

    broken = []
    for url, anchor in candidates:
        try:
            r = httpx.head(url, headers={"User-Agent": USER_AGENT}, timeout=LINK_CHECK_TIMEOUT, follow_redirects=True)
            if r.status_code >= 400:
                broken.append({"url": url, "anchor": anchor, "status": r.status_code})
        except Exception:
            # A connection failure/timeout on a link check is exactly what
            # "broken" looks like from here, unlike scrape_page's fetch
            # (where it means "couldn't analyze," not "confirmed dead") -
            # DNS failures and dead hosts throw here, not return a status.
            broken.append({"url": url, "anchor": anchor, "status": None})
    return broken


class _BrokenLinkFilterResult(BaseModel):
    pitch_subject: str
    pitch_body: str


_BROKEN_LINK_SYSTEM_PROMPT = """You are drafting a short outreach email telling a page owner \
about ONE dead link on their resource/links page, and suggesting our real published article as a \
live replacement - genuinely useful information for them, not just a pretext for a link.

Write pitch_subject and pitch_body (under 100 words): name the specific dead link's anchor text so \
they know exactly which one, mention it appears to be broken, and suggest our article as a relevant \
replacement - concrete about why it fits, not generic. No flattery filler, no exclamation points, \
sound like someone who actually visited the page.

Return JSON matching the required schema only."""


def research_broken_links(website_id: int, seranking: SERankingClient, deepseek: DeepSeekClient) -> list[dict]:
    wp_config = db_client.get_website_wp_config(website_id) or {}
    site_name = wp_config.get("name") or f"website {website_id}"
    published = db_client.get_published_articles(website_id, limit=1)
    if not published:
        return []
    our_article = published[0]

    _, results = _find_resource_pages(website_id, seranking, "resources links useful sites")
    if not results:
        return []

    opportunities = []
    for r in results[:MAX_RESOURCE_PAGES_TO_CRAWL]:
        page_url = r.get("link")
        if not page_url:
            continue
        broken = _find_broken_links(page_url)
        if not broken:
            continue
        dead = broken[0]  # one confirmed dead link is enough to justify the outreach

        user_prompt = (
            f"Our site: {site_name}\n"
            f"Our article to suggest: \"{our_article['title']}\" - {our_article['wp_post_url']}\n\n"
            f"Page with the dead link: {page_url} ({r.get('title')})\n"
            f"Dead link: {dead['url']} (anchor text: \"{dead['anchor']}\", "
            f"{'HTTP ' + str(dead['status']) if dead['status'] else 'unreachable'})"
        )
        drafted = deepseek.structured_call(
            _BROKEN_LINK_SYSTEM_PROMPT, user_prompt, _BrokenLinkFilterResult, label="offpage_broken_link_draft",
        ).model_dump()
        draft = f"Subject: {drafted['pitch_subject']}\n\n{drafted['pitch_body']}"

        opportunities.append({
            "target_url": page_url,
            "target_domain": _domain(page_url),
            "title": r.get("title"),
            "signal_summary": f"Dead link found: \"{dead['anchor']}\" -> {dead['url']}",
            "contact_info": page_url,
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
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        # search_reddit() itself degrades silently to [] (the right call for
        # the article pipeline, where a missing Reddit key shouldn't fail a
        # whole article run) - but a dashboard-triggered job that "succeeds"
        # with 0 results and no explanation looks identical to a real "no
        # threads found," which is misleading. Fail loudly here instead so
        # the job's error_message tells the real story.
        raise RuntimeError(
            "Reddit API credentials not configured (REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET) - "
            "this channel can't search until they're set."
        )
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


CHANNELS = {
    "directory": research_directories,
    "resource_page": research_resource_pages,
    "broken_link": research_broken_links,
    "social": research_social,
}


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
