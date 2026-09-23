"""Dashboard-triggered backlink gap analysis. The actual work (SERP lookup,
SE Ranking backlink pulls, spam/relevance filtering) lives in
article-pipeline/tools/backlink_gap.py - a separate Python environment from
this FastAPI app, so triggering it means spawning that venv's interpreter
as a subprocess rather than importing it directly. Fires it in the
background and the dashboard polls the job row rather than blocking the
request."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.clients import supabase_client as db

router = APIRouter(tags=["backlinks"])

# A job stuck at "running" past this is treated as dead, not slow - measured
# directly: two real jobs got orphaned by a server redeploy killing the
# subprocess mid-run and sat at "running" for hours/days with no way to
# retry. Real completed runs have taken up to ~25 minutes under today's SE
# Ranking latency, so this stays well above that instead of flagging a
# merely-slow run as stale.
JOB_STALE_MINUTES = 30

ARTICLE_PIPELINE_DIR = Path(__file__).resolve().parents[3] / "article-pipeline"
ARTICLE_PIPELINE_PYTHON = ARTICLE_PIPELINE_DIR / ".venv" / "bin" / "python"

DEFAULT_TOP_N = 2  # was 8 - checking all 8 competitors' full backlink profiles
                   # (up to 1,200 SE Ranking credit lookups) made this the
                   # slowest step of a run, especially with today's SE
                   # Ranking SERP latency stacked on top. 2 covers the
                   # highest-signal competitors at ~1/4 the cost and time.
DEFAULT_LIMIT = 150

# The "gap" signal is a domain PROVEN to link to more than one page on this
# exact topic, not just any single relevant backlink. min_competitors=1 was
# fine when top_n=8 (linking to 1 of 8 was still a real, if weaker, signal),
# but with only 2 competitors checked, requiring just 1 stops filtering for
# anything - it accepts a domain that linked to either page, no different
# from an ordinary single backlink. Raised to 2 (both) to keep the actual
# "repeat linker" signal this technique is built on, matching
# backlink_gap.py's own MIN_COMPETITORS_LINKED default.
DEFAULT_MIN_COMPETITORS = 2


class BacklinkGapTrigger(BaseModel):
    top_n: int = DEFAULT_TOP_N
    limit: int = DEFAULT_LIMIT
    min_competitors: int = DEFAULT_MIN_COMPETITORS


class CandidateStatusUpdate(BaseModel):
    status: Literal["new", "contacted", "replied", "linked", "rejected"]


@router.post("/keywords/{keyword_id}/backlink-gap")
def trigger_backlink_gap(keyword_id: int, body: BacklinkGapTrigger = BacklinkGapTrigger()):
    keyword = db.get_keyword(keyword_id)
    if keyword is None:
        raise HTTPException(status_code=404, detail="Keyword not found")
    if not ARTICLE_PIPELINE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="article-pipeline venv not found on this server")

    job = db.start_backlink_gap_job(keyword_id, keyword["website_id"])
    log_path = ARTICLE_PIPELINE_DIR / "logs" / f"backlink_gap_job_{job['job_id']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "w") as log_file:
        subprocess.Popen(
            [
                str(ARTICLE_PIPELINE_PYTHON), "tools/backlink_gap.py", keyword["keyword"],
                "--top-n", str(body.top_n), "--limit", str(body.limit),
                "--min-competitors", str(body.min_competitors),
                "--job-id", str(job["job_id"]),
                "--website-id", str(keyword["website_id"]),
                "--keyword-id", str(keyword_id),
            ],
            cwd=str(ARTICLE_PIPELINE_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # survives this request/worker without becoming a zombie tied to it
        )

    return {"job_id": job["job_id"], "status": job["status"]}


@router.get("/backlink-jobs/{job_id}")
def get_backlink_job(job_id: int):
    job = db.get_backlink_gap_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "running":
        started = datetime.fromisoformat(job["started_at"])
        if (datetime.now(timezone.utc) - started).total_seconds() > JOB_STALE_MINUTES * 60:
            job = db.mark_backlink_gap_job_stale(job_id) or job
    return job


@router.get("/keywords/{keyword_id}/backlink-candidates")
def list_backlink_candidates(keyword_id: int):
    return db.list_backlink_candidates(keyword_id)


@router.patch("/backlink-candidates/{candidate_id}")
def update_backlink_candidate(candidate_id: int, body: CandidateStatusUpdate):
    row = db.update_backlink_candidate_status(candidate_id, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return row


@router.post("/backlink-candidates/{candidate_id}/draft-outreach")
def draft_backlink_outreach(candidate_id: int):
    """Drafts an outreach email for one candidate - tools/backlink_outreach_draft.py
    does the work (DeepSeek call, no real credits to speak of), fast enough
    to run synchronously rather than as a polled background job."""
    if not ARTICLE_PIPELINE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="article-pipeline venv not found on this server")
    result = subprocess.run(
        [str(ARTICLE_PIPELINE_PYTHON), "tools/backlink_outreach_draft.py", str(candidate_id)],
        cwd=str(ARTICLE_PIPELINE_DIR),
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr[-2000:] or "draft generation failed")
    row = db.get_backlink_candidate(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return row
