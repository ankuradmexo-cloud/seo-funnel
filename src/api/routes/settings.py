"""Lets the SE Ranking API key be rotated from the dashboard instead of
Render/GitHub secrets + a redeploy - stored in system_config (the same
generic key-value table automation.py's pause switches use), read fresh by
SERankingClient on every construction (see src/clients/seranking_client.py
and article-pipeline/clients/seranking_client.py), so a change here takes
effect on the very next run with no restart needed. Falls back to the
SERANKING_API_KEY env var whenever no override is set. The key itself is
never returned by this API - only whether an override exists and a masked
tail, so a browser network tab can't leak it."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.clients import supabase_client as db

router = APIRouter(tags=["settings"])


class ApiKeyUpdate(BaseModel):
    key: str


def _mask(key: Optional[str]) -> Optional[str]:
    if not key or len(key) < 4:
        return None
    return f"****{key[-4:]}"


@router.get("/settings/seranking-api-key")
def get_seranking_api_key():
    override = db.get_config("seranking_api_key")
    return {"override_set": bool(override), "masked": _mask(override)}


@router.post("/settings/seranking-api-key")
def set_seranking_api_key(body: ApiKeyUpdate):
    key = body.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="Key cannot be empty")
    db.set_config("seranking_api_key", key)
    return {"override_set": True, "masked": _mask(key)}


@router.delete("/settings/seranking-api-key")
def clear_seranking_api_key():
    """Clears the override - back to whatever SERANKING_API_KEY is set to
    in this service's own environment."""
    db.delete_config("seranking_api_key")
    return {"override_set": False, "masked": None}
