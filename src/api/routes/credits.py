from fastapi import APIRouter

from src.clients import balances
from src.clients import supabase_client as db

router = APIRouter(tags=["credits"])


@router.get("/credits")
def credits():
    """Live provider balances, plus the result of the last pre-run check.

    The live read is what the dashboard shows; the stored preflight is what
    actually stopped a run, so both are returned - a run bounced an hour ago
    for an empty balance that has since been topped up should read as
    resolved, not as a standing failure.
    """
    live = balances.check_balances()
    return {
        "ok": all(b.ok for b in live),
        "providers": [b.model_dump() for b in live],
        "last_preflight": db.get_config("credit_preflight"),
    }
