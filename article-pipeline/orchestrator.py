"""Wires the thirteen research/writing stages together for one keyword,
logging every step's raw output so prompts can be inspected and tuned in
isolation rather than only judged by the final article.

No database, no scheduler, no locking - this is the local experimentation
loop described in README.md. Phase 2's real architecture (LangGraph,
Postgres-backed keyword selection, hosting) comes after this converges.
"""

import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import markdown as md_lib

from clients import db_client, wordpress_client
from clients.deepseek_client import DeepSeekClient
from clients.pexels_client import search_hero_image
from clients.scrappa_client import ScrappaClient
from clients.seranking_client import SERankingClient
from config import settings
from pipeline.article_strategy import build_strategy
from pipeline.article_writer import write_article
from pipeline.backlinking import backlink_older_articles
from pipeline.categorize import choose_category
from pipeline.common_content import find_common_content
from pipeline.community_research import research_community
from pipeline.competitor_research import research_competitors
from pipeline.competitor_scraping import scrape_competitors
from pipeline.content_gap_analysis import analyze_content_gaps
from pipeline.interlinking import insert_internal_links
from pipeline.keyword_expansion import expand_keywords_per_section
from pipeline.keyword_validation import regroup_by_section, validate_keywords
from pipeline.cost_tracking import compute_run_cost
from pipeline.length_control import count_paragraphs, count_words, flesch_kincaid_grade
from pipeline.quality_gate import evaluate_run
from pipeline.reddit_research import research_reddit
from pipeline.search_intent import detect_search_intent
from pipeline.seo_qa import run_qa
from pipeline.serp_research import research_serp
from pipeline.twitter_research import research_twitter
from pipeline.vocabulary_extraction import extract_vocabulary

LOGS_DIR = Path(__file__).parent / "logs"
OUTPUT_DIR = Path(__file__).parent / "output"

REDDIT_THREAD_LIMIT = 15


def _slugify(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")[:60]


# Styling only affects the standalone-file viewing experience (double-click
# to open in a browser) - it has no bearing on the select-all/copy/paste
# workflow into SE Ranking's editor, which extracts structure (headings,
# paragraphs, tables) and ignores CSS anyway.
_ARTICLE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  background: #f7f7f5;
  color: #1a1a1a;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
.wrap { max-width: 760px; margin: 0 auto; padding: 0 24px 80px; }
.hero-img { width: 100%; height: auto; display: block; margin: 32px 0 8px; border-radius: 10px; }
.hero-credit { font-size: 0.8rem; color: #8a8a8a; margin: 0 0 40px; }
.hero-credit a { color: #8a8a8a; }
h1 {
  font-size: 2.25rem; line-height: 1.2; font-weight: 800;
  margin: 8px 0 28px; letter-spacing: -0.02em;
}
h2 {
  font-size: 1.5rem; line-height: 1.3; font-weight: 700;
  margin: 48px 0 16px; padding-top: 24px; border-top: 1px solid #e5e5e0;
}
h3 { font-size: 1.15rem; font-weight: 700; margin: 28px 0 12px; }
p { font-size: 1.06rem; line-height: 1.75; margin: 0 0 20px; color: #2b2b2b; }
ul, ol { font-size: 1.06rem; line-height: 1.75; margin: 0 0 20px; padding-left: 1.4em; }
li { margin-bottom: 8px; }
strong { font-weight: 700; color: #111; }
a { color: #1a56db; text-decoration: underline; text-decoration-color: #c7d7f7; }
table { width: 100%; border-collapse: collapse; margin: 24px 0 32px; font-size: 0.95rem; }
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid #e5e5e0; }
th { background: #efeee9; font-weight: 700; }
tr:last-child td { border-bottom: none; }
blockquote {
  margin: 24px 0; padding: 4px 20px; border-left: 3px solid #d8d8d2;
  color: #555; font-style: italic;
}
code { background: #efeee9; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
hr { border: none; border-top: 1px solid #e5e5e0; margin: 40px 0; }
"""


def _hero_credit_html(image: Optional[dict]) -> str:
    """Just the Pexels attribution line - required by Pexels' license
    regardless of whether the image itself is embedded inline or only set
    as a WordPress featured image."""
    if not image or not image.get("photographer"):
        return ""
    credit_link = (
        f'<a href="{image["photographer_url"]}">{image["photographer"]}</a>'
        if image.get("photographer_url") else image["photographer"]
    )
    pexels_link = f' on <a href="{image["pexels_url"]}">Pexels</a>' if image.get("pexels_url") else ""
    return f'<p class="hero-credit">Photo by {credit_link}{pexels_link}</p>\n'


def _hero_image_html(image: Optional[dict]) -> str:
    """Image tag + credit, for the standalone local-file preview only -
    that file has no separate "featured image" slot the way a WordPress
    theme does, so the image has to be embedded directly in the body."""
    if not image:
        return ""
    alt = (image.get("alt") or "").replace('"', "&quot;")
    return f'<img class="hero-img" src="{image["url"]}" alt="{alt}" width="1200" height="600">\n{_hero_credit_html(image)}'


def _write_html(
    article_markdown: str, title: str, meta_description: str, path: Path,
    image: Optional[dict] = None,
) -> None:
    """Renders the article as real, styled HTML - opening this in a browser
    and copying the *rendered* page (not the raw .md text) is what carries
    real <h1>-<h3> tags into a rich-text paste target. Pasting the raw
    markdown file's text left every heading as literal '##' characters,
    which is why the first scored article showed 0/32 headings despite
    having 30 of them in the source."""
    body_html = md_lib.markdown(article_markdown, extensions=["extra"])
    hero_html = _hero_image_html(image)
    doc = (
        f"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">\n"
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f"<meta name=\"description\" content=\"{meta_description}\">\n"
        f"<style>{_ARTICLE_CSS}</style>\n"
        f'</head><body>\n<div class="wrap">\n{hero_html}{body_html}\n</div>\n</body></html>\n'
    )
    path.write_text(doc)


def _strip_leading_title_heading(article_markdown: str, title: str) -> str:
    """WordPress's own theme already renders the post title (from the
    `title` field passed to create_post) above the content - the article's
    own leading "# {title}" line (guaranteed present by article_writer.py's
    _ensure_title_heading, needed for the local-file preview which has no
    separate post-title slot) would otherwise show up a second time,
    right below WordPress's own rendering of it."""
    stripped = article_markdown.lstrip()
    expected = f"# {title}"
    if stripped.startswith(expected):
        return stripped[len(expected):].lstrip("\n")
    return article_markdown


def _wp_body_html(article_markdown: str, title: str, image: Optional[dict]) -> str:
    """Just the post body - converted article text, no standalone-document
    wrapper/CSS (that's only for the local-file preview in _write_html).
    The image itself is NOT embedded here - it's set as the post's actual
    featured_media (see _publish_to_wordpress), which the WordPress theme
    renders on its own; embedding it again in the body would duplicate it.
    Only the Pexels attribution credit line still needs to be in the body,
    since that's a license requirement independent of how the image is
    displayed."""
    body = _strip_leading_title_heading(article_markdown, title)
    return _hero_credit_html(image) + md_lib.markdown(body, extensions=["extra"])


def _publish_to_wordpress(
    website_id: Optional[int], keyword_id: Optional[int], slug: str, outline: dict, article_markdown: str,
    hero_image: Optional[dict], quality_gate: dict, cost_usd: float, interlinking_candidates: list[dict],
    deepseek: DeepSeekClient, log_step,
) -> dict:
    """Publishes (or explains why it didn't) - never raises, since a
    publish failure must not take down a run that otherwise produced a good
    article. A quality_gate verdict of "fail" always blocks publishing;
    "warn" and "pass" both go live, per the current decision that there is
    no draft/review step anywhere in this pipeline."""
    out = {
        "wp_publish_status": "skipped_no_website_id", "wp_post_id": None,
        "wp_post_url": None, "wp_seo_meta_set": False, "backlinking": None,
    }
    if website_id is None:
        return out
    if quality_gate["verdict"] == "fail":
        out["wp_publish_status"] = "skipped_gate_fail"
        return out

    wp_config = db_client.get_website_wp_config(website_id)
    if not wp_config or not wp_config.get("wp_base_url"):
        out["wp_publish_status"] = "skipped_no_wp_config"
        return out

    media_id = wordpress_client.upload_featured_image(wp_config, hero_image, outline["title"]) if hero_image else None
    html_content = _wp_body_html(article_markdown, outline["title"], hero_image)
    # Category comes from the website's own small, deliberate category list
    # (websites.category - the same field that drives niche discovery),
    # never invented - the LLM only picks which of these EXISTING categories
    # fits best. get_or_create_category means a category that's never been
    # used on WordPress yet gets created the first time it's needed, so
    # editing that list (e.g. adding a category via the dashboard) is all
    # that's needed for it to show up on WordPress too, on the next article
    # that gets assigned to it.
    available_categories = db_client.get_website_categories(website_id)
    category_id = None
    if available_categories:
        # No raw keyword string available in this function (only slug/
        # outline) - the title alone is sufficient signal, since
        # ensure_exact_phrase already guarantees the keyword phrase is in it.
        chosen_category = choose_category(deepseek, outline["title"], slug.replace("-", " "), available_categories)
        category_id = wordpress_client.get_or_create_category(wp_config, chosen_category)
    author_pool = wp_config.get("wp_author_ids") or []
    author_id = random.choice(author_pool) if author_pool else None
    post = wordpress_client.create_post(
        wp_config, outline["title"], html_content, outline["meta_description"],
        media_id, status=settings.wp_publish_status, slug=slug,
        category_id=category_id, author_id=author_id,
    )
    log_step("wordpress_publish", post or {"failed": True})
    if not post:
        out["wp_publish_status"] = "failed"
        return out

    out["wp_publish_status"] = settings.wp_publish_status
    out["wp_post_id"] = post["id"]
    out["wp_post_url"] = post["link"]
    out["wp_seo_meta_set"] = wordpress_client.set_seo_meta(
        wp_config, post["id"], outline["title"], outline["meta_description"]
    )

    db_client.record_published_article(
        website_id, keyword_id, outline["title"], post["slug"], post["id"], post["link"],
        quality_gate_verdict=quality_gate["verdict"], cost_usd=cost_usd,
        hero_image_url=(hero_image or {}).get("url"),
    )

    older_candidates = [c for c in interlinking_candidates if c.get("wp_post_id") != post["id"]]
    if older_candidates:
        backlink_report = backlink_older_articles(
            outline["title"], post["link"], older_candidates, wp_config, deepseek,
            settings.backlinking_candidate_limit,
        )
        log_step("backlinking", backlink_report)
        out["backlinking"] = backlink_report

    return out


def _search_hero_image_with_fallback(image_search_terms: str, keyword: str) -> Optional[dict]:
    """image_search_terms (models.py's ArticleOutline field) is documented
    as 2-4 CANDIDATE search phrases, comma-separated - but was being sent to
    Pexels as one single combined query. Measured on a real run ("live
    casino app"): the 4 phrases joined into one string ("person playing
    live casino on smartphone, mobile casino app screen, live dealer online
    casino, smartphone gambling app") matched zero Pexels photos, even
    though several of those phrases alone likely would have. Each candidate
    is now tried separately, in order, until one actually returns a photo."""
    terms = [t.strip() for t in (image_search_terms or "").split(",") if t.strip()]
    for term in terms:
        image = search_hero_image(term)
        if image:
            return image
    return search_hero_image(keyword)


def run_for_keyword(keyword: str, website_id: Optional[int] = None, keyword_id: Optional[int] = None) -> dict:
    """Thin wrapper around _run_for_keyword_inner() - the only job here is
    guaranteeing claim_keyword()'s 'queued' state never gets stuck. Measured
    on a real run: an unhandled exception mid-pipeline (SE Ranking's API
    rejecting a stale key) crashed out of the inner function before its own
    release_keyword() calls could run, leaving 6 real keywords silently
    stuck in 'queued' forever - invisible to both future automated
    selection and the shortlisted list. This still lets the exception
    propagate (callers like run_scheduler.py need to see and log the
    failure), it just guarantees the claim is released first either way."""
    if keyword_id is None:
        return _run_for_keyword_inner(keyword, website_id, keyword_id)
    try:
        db_client.claim_keyword(keyword_id)
        result = _run_for_keyword_inner(keyword, website_id, keyword_id)
    except Exception:
        db_client.release_keyword(keyword_id)
        raise
    if result.get("wp_publish_status") != settings.wp_publish_status:
        db_client.release_keyword(keyword_id)
    return result


def _run_for_keyword_inner(keyword: str, website_id: Optional[int] = None, keyword_id: Optional[int] = None) -> dict:
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{_slugify(keyword)}"
    run_dir = LOGS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    step_times: dict[str, float] = {}

    def log_step(name: str, data: Any) -> None:
        """Every step's raw output lands here, verbatim - the point is to be
        able to inspect exactly what a prompt produced, not a summary of it.

        name often embeds a section heading (write_section__<heading>) -
        found by testing: a heading containing "/" ("Card Withdrawals
        (Visa/Mastercard)") crashed the run, because Path silently treats
        an embedded "/" as a subdirectory that doesn't exist. Sanitize
        before it becomes part of a filename."""
        safe_name = re.sub(r"[/\\]", "-", name)
        ext = "md" if isinstance(data, str) else "json"
        path = run_dir / f"{len(step_times):02d}_{safe_name}.{ext}"
        payload = data if isinstance(data, str) else json.dumps(data, indent=2, default=str)
        path.write_text(payload)
        step_times[name] = time.monotonic()
        print(f"  [{len(step_times):02d}] {name} -> {path.name}")

    deepseek = DeepSeekClient()
    scrappa = ScrappaClient()
    seranking = SERankingClient()

    started = time.monotonic()
    print(f"Run {run_id}")

    print("Step 1: SERP research")
    serp = research_serp(scrappa, keyword, settings.competitor_count)
    log_step("serp_research", serp)

    print("Step 2: Search intent detection")
    search_intent = detect_search_intent(deepseek, keyword, serp)
    log_step("search_intent", search_intent)
    print(f"  intent: {search_intent.get('primary_intent')}")
    if not search_intent.get("serp_matches_keyword", True):
        print(
            "  NOTE: SERP looked unrelated to the keyword this run "
            "(scraping/search hiccup, not necessarily a real signal) - "
            "intent classification relied on the keyword's own wording instead."
        )

    if not search_intent.get("suitable_for_article", True):
        # Abort before any of the expensive steps - competitor scraping,
        # vocabulary extraction, outline creation, SE Ranking validation,
        # and 10+ writing calls all get skipped. Found by testing: a
        # local-intent keyword hit every structural target (word count,
        # headings both green-checked) and the real score barely moved,
        # because no article can outrank a Google Maps pack - the problem
        # was never execution quality, so there's nothing downstream worth
        # spending on.
        reason = search_intent.get("unsuitability_reason") or "not specified"
        print(f"  REJECTED: not suitable for a long-form article - {reason}")
        print("  Aborting run - no article will be written for this keyword.")
        elapsed = time.monotonic() - started
        run_summary = {
            "run_id": run_id,
            "keyword": keyword,
            "rejected": True,
            "rejection_reason": reason,
            "search_intent": search_intent.get("primary_intent"),
            "elapsed_seconds": round(elapsed, 1),
            "deepseek_calls": deepseek.calls_made,
            "scrappa_calls": scrappa.calls_made,
            "seranking_calls": seranking.calls_made,
            "log_dir": str(run_dir),
        }
        (run_dir / "_run_summary.json").write_text(json.dumps(run_summary, indent=2))
        print(f"\nRejected in {elapsed:.0f}s - {deepseek.calls_made} DeepSeek call(s), no article written.")
        return run_summary

    print("Step 3: Competitor page scraping")
    scraped_competitors = scrape_competitors(serp["top_results"], html_dir=run_dir / "html")
    log_step("competitor_scraping", scraped_competitors)
    scraped_count = sum(1 for c in scraped_competitors if c["scraped"])
    print(f"  scraped {scraped_count}/{len(scraped_competitors)} competitor pages -> {run_dir / 'html'}")

    print("Step 4: Structure & quality analysis")
    competitor_research = research_competitors(deepseek, keyword, scraped_competitors)
    log_step("competitor_research", competitor_research)

    print("Step 5: Common content (core signal)")
    common_content = find_common_content(deepseek, keyword, scraped_competitors)
    log_step("common_content", common_content)
    print(f"  {len(common_content.get('core_topics') or [])} core topics found")

    print("Step 6: Vocabulary extraction")
    vocabulary = extract_vocabulary(deepseek, keyword, scraped_competitors)
    log_step("vocabulary_extraction", vocabulary)
    print(f"  {len(vocabulary.get('terms') or [])} vocabulary terms kept")

    print("Step 7: Content gap analysis")
    content_gaps = analyze_content_gaps(
        deepseek, keyword, competitor_research, common_content, vocabulary, scraped_competitors,
    )
    log_step("content_gap_analysis", content_gaps)

    print("Step 8: Trend research (Reddit + Twitter)")
    reddit_threads = research_reddit(keyword, limit=REDDIT_THREAD_LIMIT)
    log_step("reddit_research", reddit_threads)
    twitter_threads = research_twitter(keyword, limit=REDDIT_THREAD_LIMIT)
    log_step("twitter_research", twitter_threads)
    print(
        f"  {len(reddit_threads)} Reddit threads"
        + ("" if reddit_threads else " (blocked or none this run)")
        + f", {len(twitter_threads)} Twitter threads (deferred - see pipeline/twitter_research.py)"
    )
    community_research = research_community(deepseek, keyword, serp, reddit_threads + twitter_threads)
    log_step("community_research", community_research)

    print("Step 9: Outline creation")
    outline = build_strategy(
        deepseek, keyword, competitor_research, common_content, vocabulary,
        content_gaps, community_research, search_intent, scraped_competitors,
    )
    log_step("article_strategy", outline)
    hard_word_total = sum(s["target_word_count"] for s in outline["sections"])
    reliable = outline.get("length_target_reliable")
    basis = (
        f"competitor-derived hard target, {outline.get('length_target_sample_size')} relevant competitors"
        if reliable else
        f"SOFT target - only {outline.get('length_target_sample_size')} competitors directly addressed "
        f"the keyword, length is LLM-judged"
    )
    print(f"  {len(outline['sections'])} sections, {hard_word_total} words ({basis})")

    print("Step 10: Per-section keyword expansion")
    section_candidates = expand_keywords_per_section(
        deepseek, keyword, outline, settings.max_keywords_per_section,
    )
    log_step("keyword_expansion", section_candidates)
    total_candidates = sum(len(sc["candidates"]) for sc in section_candidates)
    print(f"  {total_candidates} candidates across {len(section_candidates)} sections")

    print("Step 11: Bulk keyword validation (SE Ranking)")
    flat_candidates = [c for sc in section_candidates for c in sc["candidates"]]
    validated = validate_keywords(seranking, flat_candidates)
    section_keyword_map = regroup_by_section(section_candidates, validated)
    log_step("keyword_validation", {"validated": validated, "section_map": section_keyword_map})
    print(f"  {len(validated)}/{len(flat_candidates)} candidates validated with real demand")

    print("Step 12: Article writing")
    article, length_report, questions_report = write_article(
        deepseek, keyword, outline, content_gaps, community_research, vocabulary, search_intent,
        section_keyword_map,
        log_step=lambda name, text: log_step(name, text),
    )
    log_step("article_full_draft", article)
    log_step("length_enforcement", length_report)
    log_step("questions_repair", questions_report)
    print(
        f"    questions: {questions_report['checked']} required, "
        f"{questions_report['fixed']} fixed, {questions_report['unfixable']} unfixable, "
        f"{questions_report['skipped']} skipped as off-topic"
    )
    for sq in questions_report.get("skipped_questions") or []:
        print(f"      skipped: {sq['question'][:60]} - {sq['why_not_relevant'][:80]}")
    for row in length_report:
        r = row.get("readability") or {}
        k = row.get("keyword_coverage") or {}
        sh = row.get("subheading_coverage") or {}
        print(
            f"    {row['heading'][:40]:<40} {row['before']:>5} -> {row['after']:>5} words "
            f"(target {row['target']}, paragraphs {row['paragraphs']}/{row['target_paragraphs']}, "
            f"{row['rounds']} rounds, {row['status']}) "
            f"| grade {r.get('grade_before')} -> {r.get('grade_after')} ({r.get('status')}) "
            f"| keywords {k.get('missing_before', 0)}->{k.get('missing_after', 0)} missing ({k.get('status')}) "
            f"| subheadings {sh.get('missing_before', 0)}->{sh.get('missing_after', 0)} missing ({sh.get('status', 'n/a')})"
        )

    print("Step 13: SEO / content QA")
    qa_report = run_qa(
        deepseek, keyword, article, outline,
        community_research.get("real_questions_to_answer") or [], section_keyword_map,
    )
    log_step("seo_qa", qa_report)

    hero_image = _search_hero_image_with_fallback(outline.get("image_search_terms"), keyword)
    log_step("hero_image", hero_image or {"found": False})

    print("Step 14: Interlinking (to already-published articles on this site)")
    interlinking_candidates = db_client.get_published_articles(website_id, settings.interlinking_candidate_limit) \
        if website_id is not None else []
    article, interlinking_report = insert_internal_links(article, interlinking_candidates, deepseek)
    log_step("interlinking", interlinking_report)
    print(f"  {len(interlinking_candidates)} candidate(s), {interlinking_report['links_inserted']} link(s) inserted")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(keyword)
    md_output_path = OUTPUT_DIR / f"{slug}.md"
    md_output_path.write_text(article)

    html_output_path = OUTPUT_DIR / f"{slug}.html"
    _write_html(article, outline["title"], outline["meta_description"], html_output_path, image=hero_image)

    elapsed = time.monotonic() - started
    statuses = [r["status"] for r in length_report]
    hard_targets_used = {
        "target_word_count": hard_word_total,
        "target_paragraph_count": sum(s["target_paragraph_count"] for s in outline["sections"]),
        "target_heading_count": sum(len(s.get("subheadings") or []) for s in outline["sections"]),
    }
    run_summary = {
        "run_id": run_id,
        "keyword": keyword,
        "elapsed_seconds": round(elapsed, 1),
        "deepseek_calls": deepseek.calls_made,
        "deepseek_tokens": deepseek.total_tokens,
        "scrappa_calls": scrappa.calls_made,
        "seranking_calls": seranking.calls_made,
        "search_intent": search_intent.get("primary_intent"),
        "reddit_threads_found": len(reddit_threads),
        "twitter_threads_found": len(twitter_threads),
        "competitors_scraped": f"{scraped_count}/{len(scraped_competitors)}",
        "core_topics_found": len(common_content.get("core_topics") or []),
        "vocabulary_terms_kept": len(vocabulary.get("terms") or []),
        "outline_sections": len(outline["sections"]),
        "length_target_reliable": reliable,
        "length_target_sample_size": outline.get("length_target_sample_size"),
        "hard_targets": hard_targets_used,
        "candidates_generated": total_candidates,
        "candidates_validated": len(validated),
        # article_word_count: naive .split() on raw markdown (counts '##',
        # '**', table pipes as words). article_word_count_clean: markdown
        # stripped first - what a reader or SE Ranking's scorer actually sees.
        "article_word_count": len(article.split()),
        "article_word_count_clean": count_words(article),
        "article_paragraph_count": count_paragraphs(article),
        "article_reading_grade": flesch_kincaid_grade(article),
        "sections_readability_fixed": sum(1 for r in length_report if r.get("readability", {}).get("rounds", 0) > 0),
        "sections_readability_rejected": sum(1 for r in length_report if r.get("readability", {}).get("status") == "entity_rejected"),
        "keywords_missing_before": sum(r.get("keyword_coverage", {}).get("missing_before", 0) for r in length_report),
        "keywords_missing_after": sum(r.get("keyword_coverage", {}).get("missing_after", 0) for r in length_report),
        "subheadings_missing_before": sum(r.get("subheading_coverage", {}).get("missing_before", 0) for r in length_report),
        "subheadings_missing_after": sum(r.get("subheading_coverage", {}).get("missing_after", 0) for r in length_report),
        "questions_required": questions_report["checked"],
        "questions_fixed": questions_report["fixed"],
        "questions_unfixable": questions_report["unfixable"],
        "questions_skipped_off_topic": questions_report["skipped"],
        "enforce_target_words": sum(r["enforce_target"] for r in length_report),
        "condense_calls": sum(r["rounds"] for r in length_report),
        "truncation_retries": deepseek.truncation_retries,
        "sections_in_band": statuses.count("in_band"),
        "sections_stalled": statuses.count("stalled") + statuses.count("rounds_exhausted"),
        "sections_entity_rejected": statuses.count("entity_rejected"),
        "sections_under_target": statuses.count("under_target"),
        "qa_issue_count": len(qa_report.get("issues") or []),
        "title": outline["title"],
        "meta_description": outline["meta_description"],
        "hero_image_found": bool(hero_image),
        "hero_image_url": (hero_image or {}).get("url"),
        "md_output_path": str(md_output_path),
        "html_output_path": str(html_output_path),
        "log_dir": str(run_dir),
        "interlinking": interlinking_report,
    }
    run_summary["quality_gate"] = evaluate_run(run_summary)
    run_summary["cost"] = compute_run_cost(deepseek.call_log, scrappa.calls_made, seranking.calls_made)

    print("Step 15: WordPress publish")
    wp_result = _publish_to_wordpress(
        website_id, keyword_id, slug, outline, article, hero_image, run_summary["quality_gate"],
        run_summary["cost"]["total_cost_usd"], interlinking_candidates, deepseek, log_step,
    )
    run_summary.update(wp_result)
    # release_keyword() on a non-publish outcome now happens once, in the
    # run_for_keyword() wrapper - it checks this same run_summary dict
    # after _run_for_keyword_inner() returns, so nothing needed here.

    (run_dir / "_run_summary.json").write_text(json.dumps(run_summary, indent=2))

    gate = run_summary["quality_gate"]
    cost = run_summary["cost"]
    print(f"\nDone in {elapsed:.0f}s. Quality gate: {gate['verdict'].upper()}")
    for reason in gate["reasons"]:
        print(f"  - {reason}")
    print(
        f"Cost: ${cost['total_cost_usd']:.4f} total "
        f"(DeepSeek ${cost['deepseek']['cost_usd']:.4f} / {cost['deepseek']['total_tokens']} tokens, "
        f"Scrappa ${cost['scrappa']['cost_usd']:.4f}, SE Ranking ${cost['seranking']['cost_usd']:.4f})"
    )
    print(f"Markdown: {md_output_path}")
    print(f"HTML (open in browser, select all, copy, paste into the editor): {html_output_path}")
    print(f"WordPress: {run_summary['wp_publish_status']}" + (f" -> {run_summary['wp_post_url']}" if run_summary.get("wp_post_url") else ""))
    print(f"Logs: {run_dir}")
    return run_summary
