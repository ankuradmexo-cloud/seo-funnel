"""Provider balance check for the article pipeline, feeding alerts.py.

The keyword pipeline already has a full preflight (src/clients/balances.py)
sized for its own per-run cost. Articles spend far less per run, so this uses
its own floors: enough for roughly a day's articles (an article is ~$0.05
DeepSeek, ~150 SE Ranking credits, a few Scrappa credits). It only reports -
it never blocks a run - and any provider whose balance endpoint can't be
reached is reported as unchecked rather than guessed at.
"""

from datetime import datetime

import httpx

from clients.seranking_client import _resolve_api_key
from config import settings

SERANKING_MIN_CREDITS = 1000
SCRAPPA_MIN_CREDITS = 100
DEEPSEEK_MIN_USD = 0.50


def _unchecked(provider: str, required, unit: str, err: Exception) -> dict:
    return {"provider": provider, "ok": True, "checked": False, "required": required,
            "unit": unit, "detail": f"balance check unavailable: {err}"}


def _seranking() -> dict:
    try:
        r = httpx.get("https://api.seranking.com/v1/account/credits",
                      headers={"Authorization": f"Token {_resolve_api_key()}"}, timeout=20)
        r.raise_for_status()
        body = r.json()
        totals = body.get("totals") or {}
        remaining = sum(float((totals.get(b) or {}).get("remaining") or 0)
                        for b in ("subscription", "addon", "wallet"))
        access = body.get("access") or {}
        if not access.get("can_use_data_api", True):
            return {"provider": "seranking", "ok": False, "remaining": remaining,
                    "required": SERANKING_MIN_CREDITS, "unit": "credits",
                    "detail": f"Data API access is disabled ({access.get('primary_reason') or 'access denied'})",
                    "expires_at": body.get("credit_expire_at")}
        ok = remaining >= SERANKING_MIN_CREDITS
        return {"provider": "seranking", "ok": ok, "remaining": remaining,
                "required": SERANKING_MIN_CREDITS, "unit": "credits",
                "detail": None if ok else f"{remaining:,.0f} credits left, below the {SERANKING_MIN_CREDITS:,} floor",
                "expires_at": str(body["credit_expire_at"]) if body.get("credit_expire_at") else None}
    except Exception as e:  # noqa: BLE001
        return _unchecked("seranking", SERANKING_MIN_CREDITS, "credits", e)


def _scrappa() -> dict:
    try:
        r = httpx.get("https://scrappa.co/api/account/usage",
                      headers={"x-api-key": settings.scrappa_api_key}, timeout=20)
        r.raise_for_status()
        body = r.json()
        usable = (body.get("credits") or {}).get("usable")
        if usable is None:
            usable = body.get("balance")
        usable = float(usable or 0)
        ok = usable >= SCRAPPA_MIN_CREDITS
        return {"provider": "scrappa", "ok": ok, "remaining": usable, "required": SCRAPPA_MIN_CREDITS,
                "unit": "credits",
                "detail": None if ok else f"{usable:,.0f} credits left, below the {SCRAPPA_MIN_CREDITS:,} floor"}
    except Exception as e:  # noqa: BLE001
        return _unchecked("scrappa", SCRAPPA_MIN_CREDITS, "credits", e)


def _deepseek() -> dict:
    try:
        r = httpx.get("https://api.deepseek.com/user/balance",
                      headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, timeout=20)
        r.raise_for_status()
        body = r.json()
        usd = float(next((b.get("total_balance") for b in body.get("balance_infos") or []
                          if b.get("currency") == "USD"), 0) or 0)
        available = bool(body.get("is_available", True))
        ok = available and usd >= DEEPSEEK_MIN_USD
        detail = None
        if not ok:
            detail = ("account is not available for requests" if not available
                      else f"${usd:.2f} left, below the ${DEEPSEEK_MIN_USD:.2f} floor")
        return {"provider": "deepseek", "ok": ok, "remaining": usd, "required": DEEPSEEK_MIN_USD,
                "unit": "USD", "detail": detail}
    except Exception as e:  # noqa: BLE001
        return _unchecked("deepseek", DEEPSEEK_MIN_USD, "USD", e)


def check_balances() -> list[dict]:
    """Free account-metadata reads; no credits are consumed."""
    return [_seranking(), _scrappa(), _deepseek()]
