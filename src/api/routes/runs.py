from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from src.clients import supabase_client as db

router = APIRouter(tags=["runs"])


@router.get("/runs")
def list_runs(website_id: Optional[int] = None, limit: int = Query(50, le=200)):
    return db.list_runs(website_id=website_id, limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run
