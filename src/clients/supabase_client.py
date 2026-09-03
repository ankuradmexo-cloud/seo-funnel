from typing import Optional
from datetime import datetime, timezone

from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions

from src.config import settings
from src.models.schemas import Niche, Website

# Default postgrest_client_timeout is 120s, unset anywhere else in this codebase -
# tighten it to match the other API clients (30s) so a slow/stuck DB connection
# fails fast instead of hanging the whole pipeline run.
_client: Client = create_client(
    settings.supabase_url,
    settings.supabase_key,
    options=SyncClientOptions(postgrest_client_timeout=30),
)


def get_active_websites() -> list[Website]:
    resp = _client.table("websites").select("*").eq("active", True).execute()
    return [Website(**row) for row in resp.data]


def get_all_websites() -> list[Website]:
    resp = _client.table("websites").select("*").order("website_id").execute()
    return [Website(**row) for row in resp.data]


def get_existing_keywords(website_id: int, niche_id: int) -> list[str]:
    resp = (
        _client.table("keywords")
        .select("keyword")
        .eq("website_id", website_id)
        .eq("niche_id", niche_id)
        .execute()
    )
    return [row["keyword"] for row in resp.data]


# --- niches --------------------------------------------------------------

def get_active_niche_names(website_id: int) -> list[str]:
    resp = (
        _client.table("niches")
        .select("name")
        .eq("website_id", website_id)
        .in_("status", ["active", "exhausted"])
        .execute()
    )
    return [row["name"] for row in resp.data]


def create_niche(website_id: int, name: str, source: str = "seed") -> dict:
    resp = (
        _client.table("niches")
        .upsert(
            {"website_id": website_id, "name": name, "source": source},
            on_conflict="website_id,name",
        )
        .execute()
    )
    return resp.data[0]


def get_next_niche(website_id: int) -> Optional[Niche]:
    """Round-robin: whichever active niche has waited longest (or was never
    processed) goes next, so every niche gets a turn instead of all of them
    being hit - and re-worded - in a single run."""
    resp = (
        _client.table("niches")
        .select("*")
        .eq("website_id", website_id)
        .eq("status", "active")
        .order("last_processed_at", desc=False, nullsfirst=True)
        .limit(1)
        .execute()
    )
    return Niche(**resp.data[0]) if resp.data else None


def mark_niche_processed(niche_id: int, exhausted: bool = False) -> None:
    niche = _client.table("niches").select("times_processed").eq("niche_id", niche_id).execute()
    times_processed = (niche.data[0]["times_processed"] if niche.data else 0) + 1
    _client.table("niches").update(
        {
            "times_processed": times_processed,
            "last_processed_at": datetime.now(timezone.utc).isoformat(),
            "status": "exhausted" if exhausted else "active",
        }
    ).eq("niche_id", niche_id).execute()


def upsert_keyword(row: dict) -> dict:
    """Upserts and returns the stored row (including keyword_id) so callers can
    attach agent_calls to it."""
    resp = (
        _client.table("keywords")
        .upsert(row, on_conflict="website_id,normalized_keyword")
        .execute()
    )
    return resp.data[0]


def upsert_keywords_bulk(rows: list[dict], chunk_size: int = 500) -> list[dict]:
    """Batched upsert - one HTTP call per chunk instead of one per row. Needed
    now that a single niche pass can produce thousands of candidates; looping
    upsert_keyword() per row would mean thousands of sequential round-trips."""
    stored: list[dict] = []
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        resp = (
            _client.table("keywords")
            .upsert(chunk, on_conflict="website_id,normalized_keyword")
            .execute()
        )
        stored.extend(resp.data)
    return stored


def log_agent_call(row: dict) -> None:
    _client.table("agent_calls").insert(row).execute()


def list_keywords(
    website_id: Optional[int] = None,
    status: Optional[str] = None,
    niche_id: Optional[int] = None,
    run_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    query = (
        _client.table("keywords")
        .select("*, niches(name)")
        .order("last_updated", desc=True)
    )
    if website_id is not None:
        query = query.eq("website_id", website_id)
    if status is not None:
        query = query.eq("status", status)
    if niche_id is not None:
        query = query.eq("niche_id", niche_id)
    if run_id is not None:
        query = query.eq("run_id", run_id)
    resp = query.range(offset, offset + limit - 1).execute()
    return resp.data


def get_keyword(keyword_id: int) -> Optional[dict]:
    resp = (
        _client.table("keywords")
        .select("*, niches(name)")
        .eq("keyword_id", keyword_id)
        .execute()
    )
    return resp.data[0] if resp.data else None


def list_niches(website_id: int) -> list[dict]:
    resp = (
        _client.table("niches")
        .select("*")
        .eq("website_id", website_id)
        .order("name")
        .execute()
    )
    return resp.data


def funnel_counts(website_id: Optional[int] = None) -> dict[str, int]:
    """Reads the keyword_status_counts view - see status_counts_by_website."""
    q = _client.table("keyword_status_counts").select("*")
    if website_id is not None:
        q = q.eq("website_id", website_id)
    out = {s: 0 for s in KEYWORD_STATUSES}
    for row in q.execute().data:
        out[row["status"]] = out.get(row["status"], 0) + row["count"]
    return out


def list_agent_calls(
    keyword_id: Optional[int] = None,
    website_id: Optional[int] = None,
    stage: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    query = _client.table("agent_calls").select("*").order("created_at", desc=True)
    if keyword_id is not None:
        query = query.eq("keyword_id", keyword_id)
    if website_id is not None:
        query = query.eq("website_id", website_id)
    if stage is not None:
        query = query.eq("stage", stage)
    resp = query.limit(limit).execute()
    return resp.data


def start_run(website_id: int, niche_id: Optional[int] = None) -> dict:
    resp = (
        _client.table("pipeline_runs")
        .insert({"website_id": website_id, "niche_id": niche_id, "status": "running"})
        .execute()
    )
    return resp.data[0]


def finish_run(
    run_id: int,
    status: str,
    candidates_found: int,
    shortlisted_count: int,
    error_message: Optional[str] = None,
) -> None:
    _client.table("pipeline_runs").update(
        {
            "status": status,
            "candidates_found": candidates_found,
            "shortlisted_count": shortlisted_count,
            "error_message": error_message,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("run_id", run_id).execute()


def list_runs(website_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    query = _client.table("pipeline_runs").select("*").order("started_at", desc=True)
    if website_id is not None:
        query = query.eq("website_id", website_id)
    resp = query.limit(limit).execute()
    return resp.data


def last_successful_run_for_niche(niche_id: int, exclude_run_id: int) -> Optional[dict]:
    """Most recent completed run for this niche, ignoring the current one.
    Used to require two consecutive zero-approval runs before retiring a niche,
    so a single unlucky run (thin discovery, a transient API outage) can't kill
    a viable niche. Only 'success' runs count - a crashed or budget-truncated
    run is not evidence about the niche."""
    resp = (
        _client.table("pipeline_runs")
        .select("*")
        .eq("niche_id", niche_id)
        .eq("status", "success")
        .neq("run_id", exclude_run_id)
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_run(run_id: int) -> Optional[dict]:
    resp = _client.table("pipeline_runs").select("*").eq("run_id", run_id).execute()
    return resp.data[0] if resp.data else None


# --- semantic expansion ----------------------------------------------------

def get_unexpanded_anchors(website_id: int, limit: int = 1) -> list[dict]:
    """Proven keywords (shortlisted or published) not yet mined for adjacent
    intents, best-scored first."""
    resp = (
        _client.table("keywords")
        .select("*")
        .eq("website_id", website_id)
        .in_("status", ["shortlisted", "published"])
        .eq("expanded", False)
        .order("judge_score", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data


def mark_keyword_expanded(keyword_id: int) -> None:
    _client.table("keywords").update(
        {"expanded": True, "expanded_at": datetime.now(timezone.utc).isoformat()}
    ).eq("keyword_id", keyword_id).execute()


def log_expansion_round(
    anchor_keyword_id: int, round_num: int, new_keywords_found: int, exhausted: bool
) -> None:
    _client.table("expansions").insert(
        {
            "anchor_keyword_id": anchor_keyword_id,
            "round": round_num,
            "new_keywords_found": new_keywords_found,
            "exhausted": exhausted,
        }
    ).execute()


# --- system config / automation control ------------------------------------

def get_config(key: str, default=None):
    resp = _client.table("system_config").select("value").eq("key", key).execute()
    return resp.data[0]["value"] if resp.data else default


def set_config(key: str, value) -> None:
    _client.table("system_config").upsert(
        {"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="key",
    ).execute()


def automation_enabled() -> bool:
    return bool(get_config("automation_enabled", True))


# --- website management ----------------------------------------------------

def update_website(website_id: int, fields: dict) -> Optional[dict]:
    """Only whitelisted columns are updatable from the dashboard - a category
    edit changes what niche discovery generates next, so it's a meaningful
    control, but nothing else about the row should be editable."""
    allowed = {k: v for k, v in fields.items() if k in {"name", "category", "active"}}
    if not allowed:
        return get_website(website_id)
    resp = _client.table("websites").update(allowed).eq("website_id", website_id).execute()
    return resp.data[0] if resp.data else None


def get_website(website_id: int) -> Optional[dict]:
    resp = _client.table("websites").select("*").eq("website_id", website_id).execute()
    return resp.data[0] if resp.data else None


# --- dashboard aggregates --------------------------------------------------

KEYWORD_STATUSES = [
    "deduped", "validated", "judged", "shortlisted", "queued", "published",
]


def status_counts_by_website(website_ids: list[int]) -> dict[int, dict[str, int]]:
    """Reads the keyword_status_counts view - Postgres does the GROUP BY, so
    this is one query returning a handful of rows instead of ~18 COUNT round
    trips (which made /api/overview take 10.8s) or a client-side tally over a
    truncated 1000-row page (which silently undercounted)."""
    resp = _client.table("keyword_status_counts").select("*").execute()
    out: dict[int, dict[str, int]] = {
        wid: {s: 0 for s in KEYWORD_STATUSES} for wid in website_ids
    }
    for row in resp.data:
        bucket = out.setdefault(row["website_id"], {s: 0 for s in KEYWORD_STATUSES})
        bucket[row["status"]] = row["count"]
    return out


def niche_counts_by_website(website_ids: list[int]) -> dict[int, dict[str, int]]:
    resp = _client.table("niche_status_counts").select("*").execute()
    statuses = ("active", "exhausted", "paused")
    out: dict[int, dict[str, int]] = {wid: {s: 0 for s in statuses} for wid in website_ids}
    for row in resp.data:
        bucket = out.setdefault(row["website_id"], {s: 0 for s in statuses})
        bucket[row["status"]] = row["count"]
    return out


def shortlisted_since(iso_ts: str, website_ids: list[int]) -> dict[int, int]:
    """Per-website count of keywords shortlisted since a timestamp - 'have we
    hit today's target yet'. Bounded select rather than a view because of the
    time filter; the daily target is 2/site so this window returns single
    digits, nowhere near the 1000-row cap."""
    resp = (
        _client.table("keywords")
        .select("website_id")
        .eq("status", "shortlisted")
        .gte("last_updated", iso_ts)
        .limit(1000)
        .execute()
    )
    out: dict[int, int] = {wid: 0 for wid in website_ids}
    for row in resp.data:
        out[row["website_id"]] = out.get(row["website_id"], 0) + 1
    return out


def latest_run_per_website() -> dict[int, dict]:
    resp = (
        _client.table("pipeline_runs").select("*")
        .order("started_at", desc=True).limit(300).execute()
    )
    out: dict[int, dict] = {}
    for row in resp.data:
        out.setdefault(row["website_id"], row)
    return out




# --- api usage -------------------------------------------------------------

def record_api_usage(rows: list[dict]) -> None:
    if rows:
        _client.table("api_usage").insert(rows).execute()


def api_usage_totals() -> list[dict]:
    return _client.table("api_usage_totals").select("*").execute().data


def api_usage_by_endpoint() -> list[dict]:
    return (
        _client.table("api_usage_by_endpoint")
        .select("*")
        .order("total_credits", desc=True)
        .execute()
        .data
    )
