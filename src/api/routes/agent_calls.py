from typing import Optional
from fastapi import APIRouter, Query

from src.clients import supabase_client as db

router = APIRouter(tags=["agent-calls"])


@router.get("/agent-calls")
def list_agent_calls(
    keyword_id: Optional[int] = None,
    website_id: Optional[int] = None,
    stage: Optional[str] = None,
    limit: int = Query(50, le=200),
):
    return db.list_agent_calls(
        keyword_id=keyword_id, website_id=website_id, stage=stage, limit=limit
    )
