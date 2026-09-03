from fastapi import APIRouter
from pydantic import BaseModel

from src.clients import supabase_client as db

router = APIRouter(tags=["automation"])


class AutomationState(BaseModel):
    enabled: bool


@router.get("/automation")
def get_automation():
    """Global pause switch. The scheduler (Render cron / GitHub Actions) can't
    be stopped from here - instead every run checks this flag on startup and
    exits immediately when it's off, so no API credits are spent."""
    return {"enabled": db.automation_enabled()}


@router.post("/automation")
def set_automation(body: AutomationState):
    db.set_config("automation_enabled", body.enabled)
    return {"enabled": body.enabled}
