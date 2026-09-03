from fastapi import APIRouter

from src.clients import supabase_client as db

router = APIRouter(tags=["niches"])


@router.get("/niches")
def list_niches(website_id: int):
    return db.list_niches(website_id)
