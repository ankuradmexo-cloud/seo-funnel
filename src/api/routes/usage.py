from fastapi import APIRouter

from src.clients import supabase_client as db

router = APIRouter(tags=["usage"])

# Credit costs differ per provider and aren't interchangeable, so the dashboard
# needs the unit price to turn credits into money.
#   Scrappa    top-up packs, $0.0002-0.0003/credit depending on pack size
#   SE Ranking Wallet top-up, $50 = 250k credits
#   DeepSeek   billed per token, not credits
UNIT_COST_USD = {
    "scrappa": 0.00025,
    "seranking": 0.0002,
}


@router.get("/usage")
def usage():
    totals = db.api_usage_totals()
    for row in totals:
        unit = UNIT_COST_USD.get(row["provider"])
        credits = float(row.get("total_credits") or 0)
        avg = float(row.get("avg_credits_per_run") or 0)
        row["unit_cost_usd"] = unit
        row["total_cost_usd"] = round(credits * unit, 4) if unit else None
        row["avg_cost_per_run_usd"] = round(avg * unit, 4) if unit else None
    return {"totals": totals, "by_endpoint": db.api_usage_by_endpoint()}
