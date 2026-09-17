from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.clients import supabase_client as db

router = APIRouter(tags=["websites"])


class WebsiteUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None
    domain: Optional[str] = None
    wp_base_url: Optional[str] = None
    wp_username: Optional[str] = None
    wp_app_password: Optional[str] = None
    seo_plugin: Optional[Literal["yoast", "rankmath", "none"]] = None
    articles_per_day: Optional[int] = None
    article_automation_enabled: Optional[bool] = None
    wp_author_ids: Optional[list[int]] = None


@router.get("/websites")
def list_websites():
    return [w.model_dump() for w in db.get_all_websites()]


@router.patch("/websites/{website_id}")
def update_website(website_id: int, body: WebsiteUpdate):
    """Editing `category` changes what niche discovery generates on the next
    top-up; `active` is the per-site keyword-shortlisting switch
    (run_all_active_websites skips inactive sites entirely);
    `article_automation_enabled` is the separate per-site switch for the
    article pipeline's scheduler - the two can be toggled independently,
    e.g. keep discovering keywords for a site while holding off on writing
    articles for it. `wp_*`/`seo_plugin` configure where and how this site's
    articles get published; `articles_per_day` is that scheduler's daily cap."""
    fields = body.model_dump(exclude_none=True)
    row = db.update_website(website_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="Website not found")
    return row
