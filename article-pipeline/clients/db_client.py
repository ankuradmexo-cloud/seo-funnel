"""Talks to the same Supabase project seo-funnel's own src/clients/supabase_client.py
uses - this is a separate, minimal client (not an import of that module)
because article-pipeline runs as its own cwd-relative script
(clients/x, pipeline/x imports) rather than as part of the `src` package,
so sharing the exact same client object isn't practical without restructuring
imports across the whole repo. Every function here degrades to None/[]/no-op
when SUPABASE_URL/SUPABASE_KEY aren't set, the same graceful pattern as
clients/pexels_client.py - a run should still work with zero Supabase config,
it just skips interlinking, backlinking, and WordPress publishing.
"""

import re
from datetime import date
from typing import Optional

from config import settings

_client = None
if settings.supabase_url and settings.supabase_key:
    from supabase import create_client

    _client = create_client(settings.supabase_url, settings.supabase_key)


def configured() -> bool:
    return _client is not None


def get_website_categories(website_id: int) -> list[str]:
    """websites.category is a comma-separated free-text field that already
    drives niche discovery (e.g. "AI tools, business/SaaS software
    comparisons, productivity software, ...") - a small, deliberate, stable
    list per site, reused here as the fixed set of WordPress categories an
    article can be assigned to."""
    if _client is None:
        return []
    resp = _client.table("websites").select("category").eq("website_id", website_id).execute()
    if not resp.data or not resp.data[0].get("category"):
        return []
    raw = [c.strip() for c in resp.data[0]["category"].split(",") if c.strip()]
    # The source text is natural prose ("...consumer electronics buying
    # guides, and travel booking platforms") - strip a leading "and "/"or "
    # left over from the Oxford comma before the last item, or it becomes
    # a literal (wrong) category name.
    return [re.sub(r"^(and|or)\s+", "", c, flags=re.IGNORECASE) for c in raw]


def get_website_wp_config(website_id: int) -> Optional[dict]:
    if _client is None:
        return None
    resp = (
        _client.table("websites")
        .select("website_id, name, wp_base_url, wp_username, wp_app_password, seo_plugin, wp_author_ids")
        .eq("website_id", website_id)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_published_articles(website_id: int, limit: int = 30) -> list[dict]:
    """Interlinking candidate pool - most recently published first."""
    if _client is None:
        return []
    resp = (
        _client.table("published_articles")
        .select("article_id, title, wp_post_url, wp_post_id, keyword_id")
        .eq("website_id", website_id)
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [r for r in resp.data if r.get("wp_post_url")]


def list_all_published_articles() -> list[dict]:
    """Every recorded article, across all websites - used by
    tools/migrate_test_articles.py to find everything that needs migrating
    off the test site, oldest first (the order they originally published in)."""
    if _client is None:
        return []
    resp = _client.table("published_articles").select("*").order("published_at").execute()
    return resp.data


def record_published_article(
    website_id: int, keyword_id: Optional[int], title: str, slug: str,
    wp_post_id: Optional[int], wp_post_url: Optional[str],
    quality_gate_verdict: Optional[str] = None, cost_usd: Optional[float] = None,
    hero_image_url: Optional[str] = None,
) -> Optional[dict]:
    if _client is None:
        return None
    resp = (
        _client.table("published_articles")
        .insert({
            "website_id": website_id, "keyword_id": keyword_id, "title": title,
            "slug": slug, "wp_post_id": wp_post_id, "wp_post_url": wp_post_url,
            "quality_gate_verdict": quality_gate_verdict, "cost_usd": cost_usd,
            "hero_image_url": hero_image_url,
        })
        .execute()
    )
    if keyword_id is not None:
        _client.table("keywords").update(
            {"status": "published", "target_url": wp_post_url}
        ).eq("keyword_id", keyword_id).execute()
    return resp.data[0] if resp.data else None


def update_published_article(article_id: int, fields: dict) -> Optional[dict]:
    """Used by tools/migrate_test_articles.py to point an already-recorded
    row at a newly-created real post, replacing the disposable test one."""
    if _client is None:
        return None
    resp = _client.table("published_articles").update(fields).eq("article_id", article_id).execute()
    return resp.data[0] if resp.data else None


# --- automation control (scheduler) -----------------------------------------

def article_automation_enabled() -> bool:
    """Global kill switch - mirrors seo-funnel's own automation_enabled()
    but reads the separate 'article_automation_enabled' key, so pausing one
    pipeline never pauses the other."""
    if _client is None:
        return True
    resp = _client.table("system_config").select("value").eq("key", "article_automation_enabled").execute()
    return bool(resp.data[0]["value"]) if resp.data else True


def get_active_article_websites() -> list[dict]:
    """Websites where the per-site article switch is on - independent of
    `active`, which only gates keyword shortlisting."""
    if _client is None:
        return []
    resp = (
        _client.table("websites")
        .select("website_id, name, articles_per_day, article_automation_enabled")
        .eq("article_automation_enabled", True)
        .execute()
    )
    return resp.data


def articles_published_today(website_id: int) -> int:
    if _client is None:
        return 0
    since = f"{date.today().isoformat()}T00:00:00+00:00"
    resp = (
        _client.table("published_articles")
        .select("article_id")
        .eq("website_id", website_id)
        .gte("published_at", since)
        .execute()
    )
    return len(resp.data)


def claim_keyword(keyword_id: int) -> None:
    """Marks a keyword 'queued' the moment a run actually starts on it -
    without this, two runs (e.g. a manual trigger and the scheduler)
    started close together can both select the same top-scored shortlisted
    keyword before either finishes, since status only flipped to
    'published' at the very end. Measured on a real run: this produced two
    separate live WordPress posts for the same keyword. 'queued' is an
    existing, previously-unused step in the keywords status lifecycle
    (shortlisted -> queued -> published) - this is exactly what it's for."""
    if _client is None or keyword_id is None:
        return
    _client.table("keywords").update({"status": "queued"}).eq("keyword_id", keyword_id).execute()


def release_keyword(keyword_id: int) -> None:
    """Reverts a claimed keyword back to 'shortlisted' if the run didn't
    end up publishing (e.g. a quality-gate fail) - so it stays eligible for
    a future attempt instead of being silently stuck in 'queued' forever."""
    if _client is None or keyword_id is None:
        return
    _client.table("keywords").update({"status": "shortlisted"}).eq("keyword_id", keyword_id).execute()


def get_shortlisted_keywords_for_articles(website_id: int, limit: int) -> list[dict]:
    if _client is None or limit <= 0:
        return []
    resp = (
        _client.table("keywords")
        .select("keyword_id, keyword")
        .eq("website_id", website_id)
        .eq("status", "shortlisted")
        .order("judge_score", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data


# --- backlink gap analysis (tools/backlink_gap.py) --------------------------
# Job tracking mirrors seo-funnel's own pipeline_runs/article_jobs pattern -
# the dashboard triggers a job via FastAPI, which spawns this tool as a
# subprocess and polls this row rather than blocking on a multi-minute,
# real-credit-spending request.

def finish_backlink_gap_job(
    job_id: int, status: str, error_message: Optional[str] = None,
) -> None:
    if _client is None:
        return
    from datetime import datetime, timezone
    _client.table("backlink_gap_jobs").update({
        "status": status, "error_message": error_message,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }).eq("job_id", job_id).execute()


def record_backlink_candidates(
    job_id: int, keyword_id: int, website_id: int, candidates: list[dict],
) -> None:
    if _client is None or not candidates:
        return
    rows = [
        {
            "job_id": job_id, "keyword_id": keyword_id, "website_id": website_id,
            "referring_domain": c["referring_domain"],
            "domain_inlink_rank": c["domain_inlink_rank"],
            "competitors_linked_count": c["competitors_linked_count"],
            "sample_links": c["sample_links"],
        }
        for c in candidates
    ]
    _client.table("backlink_candidates").insert(rows).execute()
