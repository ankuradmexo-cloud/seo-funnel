from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.clients import supabase_client as db

router = APIRouter(tags=["websites"])


class WebsiteUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None


@router.get("/websites")
def list_websites():
    return [w.model_dump() for w in db.get_all_websites()]


@router.patch("/websites/{website_id}")
def update_website(website_id: int, body: WebsiteUpdate):
    """Editing `category` changes what niche discovery generates on the next
    top-up; `active` is the per-site automation switch (run_all_active_websites
    skips inactive sites entirely)."""
    fields = body.model_dump(exclude_none=True)
    row = db.update_website(website_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="Website not found")
    return row
