from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from src.clients import supabase_client as db
from src.config import settings

router = APIRouter(tags=["overview"])

# Statuses a keyword passes through after being approved. 'shortlisted' is
# waiting for n8n to pick it up; 'queued' means n8n has it; 'published' is done.
PIPELINE_STATUSES = [
    "deduped", "validated", "judged", "shortlisted", "queued", "published",
]


@router.get("/overview")
def overview():
    """Single call powering the whole dashboard - per-website progress against
    the daily target, publishable backlog, niche health and last run status.
    Built from four bulk queries rather than N queries per website."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    websites = db.get_all_websites()
    ids = [w.website_id for w in websites]
    status_counts = db.status_counts_by_website(ids)
    recent = db.shortlisted_since(since, ids)
    latest_runs = db.latest_run_per_website()
    niche_counts = db.niche_counts_by_website(ids)

    sites = []
    for w in websites:
        counts = status_counts.get(w.website_id, {})
        niches = niche_counts.get(w.website_id, {})
        run = latest_runs.get(w.website_id)
        sites.append({
            "website_id": w.website_id,
            "name": w.name,
            "category": w.category,
            "active": w.active,
            "status_counts": {s: counts.get(s, 0) for s in PIPELINE_STATUSES},
            # awaiting_publish = approved but n8n hasn't taken it yet
            "awaiting_publish": counts.get("shortlisted", 0),
            "in_progress": counts.get("queued", 0),
            "published": counts.get("published", 0),
            "shortlisted_last_24h": recent.get(w.website_id, 0),
            "daily_target": settings.max_keywords_per_site_per_day,
            "niches": {
                "active": niches.get("active", 0),
                "exhausted": niches.get("exhausted", 0),
                "paused": niches.get("paused", 0),
            },
            "last_run": {
                "run_id": run["run_id"],
                "status": run["status"],
                "started_at": run["started_at"],
                "finished_at": run["finished_at"],
                "candidates_found": run["candidates_found"],
                "shortlisted_count": run["shortlisted_count"],
                "error_message": run["error_message"],
            } if run else None,
        })

    return {
        "automation_enabled": db.automation_enabled(),
        "daily_target_per_site": settings.max_keywords_per_site_per_day,
        "websites": sites,
    }
