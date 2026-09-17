"""Step 3 - competitor page scraping.

Fetches each competitor's actual page: saves the raw HTML to disk for the
record, and extracts main readable text, structure (heading tree, table
count), and metadata (meta title/description) for later steps. Best-effort
per page - one that blocks scraping, renders via JS, or times out is marked
unscraped here and falls back to its snippet in the next step, rather than
failing the whole research phase.
"""

from pathlib import Path
from typing import Optional

from clients.page_scraper import scrape_page


def scrape_competitors(top_results: list[dict], html_dir: Optional[Path] = None) -> list[dict]:
    """html_dir, if given, gets one raw .html file per successfully scraped
    competitor - the exact page fetched, for debugging extraction quality or
    reprocessing without hitting the network again. Kept out of the JSON step
    log (competitor_scraping.json) since raw HTML would dwarf everything
    else in it; the JSON stores a filename reference instead."""
    if html_dir is not None:
        html_dir.mkdir(parents=True, exist_ok=True)

    scraped = []
    for i, r in enumerate(top_results):
        page = scrape_page(r.get("link"))

        raw_html_file = None
        if page and html_dir is not None:
            raw_html_file = f"comp_{i + 1:02d}.html"
            (html_dir / raw_html_file).write_text(page["html"], errors="ignore")

        scraped.append(
            {
                "position": r.get("position"),
                "title": r.get("title"),
                "url": r.get("link"),
                "source": r.get("source"),
                "snippet": r.get("snippet"),
                "scraped": page is not None,
                "text": page["text"] if page else None,
                "word_count": page["word_count"] if page else None,
                "paragraph_count": page["paragraph_count"] if page else None,
                "headings": page["headings"] if page else [],
                "heading_tree": page["heading_tree"] if page else [],
                "table_count": page["table_count"] if page else 0,
                "meta_title": page["meta_title"] if page else None,
                "meta_description": page["meta_description"] if page else None,
                "raw_html_file": raw_html_file,
            }
        )
    return scraped


def format_competitors_for_prompt(scraped_competitors: list[dict]) -> str:
    """The single canonical rendering of scraped competitor data into prompt
    text - full content, full heading list, meta, table count. No excerpt
    truncation. Used by every step that reasons about competitors (research,
    common content, vocabulary, gap analysis, outline) so they all see the
    same complete picture rather than each applying its own truncation. A
    competitor whose page failed to scrape falls back to its SERP
    title/snippet."""
    parts = []
    for i, c in enumerate(scraped_competitors):
        label = f"{i + 1}. {c['title']} ({c.get('source') or c['url']})"
        if c["scraped"]:
            headings = "\n".join(f"    {h['level']}: {h['text']}" for h in c["headings"])
            parts.append(
                f"{label}\n"
                f"   Meta title: {c.get('meta_title') or '(none)'}\n"
                f"   Meta description: {c.get('meta_description') or '(none)'}\n"
                f"   Word count: {c.get('word_count')} | Paragraphs: {c.get('paragraph_count')} | "
                f"Tables: {c.get('table_count')}\n"
                f"   Headings:\n{headings or '    (none found)'}\n"
                f"   Full content:\n{c['text']}"
            )
        else:
            parts.append(
                f"{label} - full page unavailable, using snippet only\n"
                f"   Snippet: {c.get('snippet') or '(none)'}"
            )
    return "\n\n".join(parts) or "(no organic results returned)"


# Below this many relevant competitors, a median isn't a real signal -
# measured directly: one run's SERP surfaced mostly generic "backpacking
# 101" content instead of gear-list-specific articles, and a median across
# those looked perfectly reasonable (1,394 words) while being the wrong
# number entirely - those pages were never trying to be a comprehensive
# gear list, so their length says nothing about what one should be.
MIN_RELEVANT_COMPETITORS = 3


# Only forum_thread is excluded by category now - product_page used to be
# excluded outright too, on the theory it meant App Store/Play Store
# listings. Measured directly: for "project management software for
# construction companies", several product_page competitors were genuine
# vendor content pages (733, 1315 words) already correctly marked
# directly_addresses_keyword=True by the relevance classifier - excluding
# them by category threw away real signal SE Ranking's own target appears
# to include, and rescaled our word target ~2.5x too high. A genuine App
# Store listing already tends to fail the relevance check on its own (it's
# not real content answering the keyword), and THIN_PAGE_WORD_RATIO below
# catches a junk product_page that slips through relevant but trivial.
NON_ARTICLE_CONTENT_TYPES = {"forum_thread"}

# A page can pass every filter (scraped, relevant, real article) and still
# be a thin stub - measured directly: a "listicle" competitor at 156 words
# with 1 heading sat in a pool where the other 8 genuine listicles ran
# 1,100-4,300 words with 8-39 headings. That single page set the whole
# range's floor, and rescaling to the range's midpoint then undershot both
# what the real competitors support and what SE Ranking's own brief asked
# for. Anything under this fraction of the pool's median word count is
# dropped from the min/max calculation - not because it's off-topic (it
# already passed that check), but because its structure doesn't reflect a
# genuine attempt at the format everyone else is producing.
THIN_PAGE_WORD_RATIO = 0.4

# A symmetric high-end trim (mirroring THIN_PAGE_WORD_RATIO) was tried and
# reverted - tested directly against "wild casino withdrawal time"'s real
# data, it made the target worse, not better. Checking SE Ranking's own
# "Competitive pages" list explained why: their active/inactive competitor
# set isn't chosen by word count or topical relevance at all - it includes
# a page from a completely different, similarly-named brand (wildz.com,
# not Wild Casino) right alongside the real ones, and the actual dividing
# line was each candidate page's own SE Ranking Content Score (a quality
# rating for that page, not this one). That's a signal their API doesn't
# expose and this pipeline has no way to compute - a statistical trim on
# our own word-count distribution was never going to reproduce a filter
# based on a completely different, inaccessible metric.


def _median(xs: list) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def compute_competitor_stats(
    scraped_competitors: list[dict],
    relevance_flags: Optional[list] = None,
    content_types: Optional[list] = None,
) -> dict:
    """Min-max range for word count, paragraph count, and heading count
    across competitors that scraped successfully, (when relevance_flags is
    given) directly address the keyword, and (when content_types is given)
    are actual articles rather than product/store pages - see
    pipeline/competitor_research.py's directly_addresses_keyword and
    CompetitorEntry.content_type.

    Range, not median: SE Ranking's own Content Editor briefs give a
    min-max range for these fields, not a single number - measured directly
    from its "Content parameters" panel. A single median number also let a
    App Store-listing competitor (structurally nothing like an article) drag
    the target toward its own shape; content_types filtering removes that
    kind of competitor from the pool entirely rather than let it distort a
    single midpoint.

    relevance_flags and content_types, if given, must be the same length and
    order as scraped_competitors. Missing/extra entries (an LLM returning a
    different count than it was given) are treated conservatively - not
    relevant / excluded - rather than risk polluting the hard target with an
    unverified or non-article page.

    "reliable" is False when fewer than MIN_RELEVANT_COMPETITORS qualify -
    the caller (article_strategy.py) is expected to fall back to soft,
    LLM-judged length guidance rather than hard-enforcing a number computed
    from too thin or too off-topic a sample."""
    pool = scraped_competitors
    if relevance_flags is not None:
        pool = [c for c, relevant in zip(pool, relevance_flags) if c["scraped"] and relevant]
    else:
        pool = [c for c in pool if c["scraped"]]

    if content_types is not None:
        types = list(content_types) + ["other"] * max(0, len(scraped_competitors) - len(content_types))
        type_by_id = {id(c): t for c, t in zip(scraped_competitors, types)}
        pool = [c for c in pool if type_by_id.get(id(c)) not in NON_ARTICLE_CONTENT_TYPES]

    if len(pool) >= 4:
        median_words = _median([c["word_count"] for c in pool])
        pool = [c for c in pool if c["word_count"] >= median_words * THIN_PAGE_WORD_RATIO]

    reliable = len(pool) >= MIN_RELEVANT_COMPETITORS

    if not pool:
        return {
            "target_word_count_min": 2000, "target_word_count_max": 3000,
            "target_paragraph_count_min": 25, "target_paragraph_count_max": 35,
            "target_heading_count_min": 8, "target_heading_count_max": 12,
            "sample_size": 0,
            "reliable": False,
        }

    def bounds(xs: list) -> tuple:
        return min(xs), max(xs)

    word_min, word_max = bounds([c["word_count"] for c in pool])
    para_min, para_max = bounds([c["paragraph_count"] for c in pool])
    head_min, head_max = bounds([len(c["headings"]) for c in pool])

    return {
        "target_word_count_min": word_min, "target_word_count_max": word_max,
        "target_paragraph_count_min": para_min, "target_paragraph_count_max": para_max,
        "target_heading_count_min": head_min, "target_heading_count_max": head_max,
        "sample_size": len(pool),
        "reliable": reliable,
    }
