"""Dashboard-triggered off-page opportunity research - directories, guest
posts, and social/forum threads. The actual work lives in
article-pipeline/tools/offpage_research.py, a separate Python environment
from this FastAPI app, so triggering it means spawning that venv's
interpreter as a subprocess, same pattern as src/api/routes/backlinks.py.
Every channel here only produces research + a drafted outreach message -
nothing is ever sent or posted automatically."""

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.clients import supabase_client as db

router = APIRouter(tags=["offpage"])

ARTICLE_PIPELINE_DIR = Path(__file__).resolve().parents[3] / "article-pipeline"
ARTICLE_PIPELINE_PYTHON = ARTICLE_PIPELINE_DIR / ".venv" / "bin" / "python"

# Same self-healing staleness check as backlinks.py - see its comment for
# the real orphaned-job incidents that motivated this.
JOB_STALE_MINUTES = 30


def _parse_ts(ts: str) -> datetime:
    """See backlinks.py's _parse_ts - datetime.fromisoformat crashes on
    Python 3.9 when Supabase trims trailing zeros off the fractional
    seconds (a real, measured bug, not hypothetical)."""
    return datetime.fromisoformat(re.sub(r"\.\d+", "", ts))

Channel = Literal["directory", "resource_page", "broken_link", "social"]


class OpportunityStatusUpdate(BaseModel):
    status: Literal["new", "contacted", "replied", "won", "rejected"]


@router.post("/websites/{website_id}/offpage/{channel}")
def trigger_offpage_research(website_id: int, channel: Channel):
    if not ARTICLE_PIPELINE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="article-pipeline venv not found on this server")

    job = db.start_offpage_job(website_id, channel)
    log_path = ARTICLE_PIPELINE_DIR / "logs" / f"offpage_job_{job['job_id']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "w") as log_file:
        subprocess.Popen(
            [
                str(ARTICLE_PIPELINE_PYTHON), "tools/offpage_research.py",
                "--channel", channel, "--website-id", str(website_id),
                "--job-id", str(job["job_id"]),
            ],
            cwd=str(ARTICLE_PIPELINE_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    return {"job_id": job["job_id"], "status": job["status"]}


@router.get("/offpage-jobs/{job_id}")
def get_offpage_job(job_id: int):
    job = db.get_offpage_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "running":
        started = _parse_ts(job["started_at"])
        if (datetime.now(timezone.utc) - started).total_seconds() > JOB_STALE_MINUTES * 60:
            job = db.mark_offpage_job_stale(job_id) or job
    return job


@router.get("/websites/{website_id}/offpage-opportunities")
def list_offpage_opportunities(website_id: int, channel: Optional[Channel] = None):
    return db.list_offpage_opportunities(website_id, channel)


@router.patch("/offpage-opportunities/{opportunity_id}")
def update_offpage_opportunity(opportunity_id: int, body: OpportunityStatusUpdate):
    row = db.update_offpage_opportunity_status(opportunity_id, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return row
