from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from src.clients import supabase_client as db

router = APIRouter(tags=["keywords"])


@router.get("/keywords")
def list_keywords(
    website_id: Optional[int] = None,
    status: Optional[str] = None,
    niche_id: Optional[int] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    return db.list_keywords(
        website_id=website_id, status=status, niche_id=niche_id, limit=limit, offset=offset
    )


@router.get("/keywords/{keyword_id}")
def get_keyword(keyword_id: int):
    row = db.get_keyword(keyword_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Keyword not found")
    return row


@router.get("/funnel-stats")
def funnel_stats(website_id: Optional[int] = None):
    return db.funnel_counts(website_id=website_id)
