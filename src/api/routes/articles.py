from typing import Optional

from fastapi import APIRouter, Query

from src.clients import supabase_client as db

router = APIRouter(tags=["articles"])


@router.get("/articles")
def list_articles(website_id: Optional[int] = Query(None)):
    return db.list_published_articles(website_id=website_id)
