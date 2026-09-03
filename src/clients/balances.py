"""Pre-run credit checks.

The pipeline spends credits on three providers in sequence, and the cheap one
goes first: a run burns ~330 Scrappa credits on autocomplete expansion before
SE Ranking is touched at all. If SE Ranking is empty, all of that Scrappa spend
is wasted - the run cannot produce a keyword without demand validation.

So every provider is checked *before* the first call to any of them. All three
balance endpoints are free account-metadata reads that consume no credits.
"""

from typing import Optional
import httpx
from pydantic import BaseModel

from src.config import settings

# Per-run requirements, derived from the cost dials rather than hardcoded, so
# raising SEEDS_PER_NICHE also raises the bar a run has to clear.
#
#   Scrappa    autocomplete BFS is seeds x (1 + breadth) calls, plus one SERP
#              fetch per judged candidate (bounded by the DeepSeek budget).
#   SE Ranking one flat 100-credit demand call, plus questions at 10 credits
#              per keyword returned - worst case every seed returns its full
#              limit.
#   DeepSeek   billed in dollars, not credits. A run's judge calls are small;
#              this floor only catches an account that is actually empty.
_AUTOCOMPLETE_BREADTH = 10
MIN_DEEPSEEK_USD = 0.10


def scrappa_credits_needed() -> int:
    return settings.seeds_per_niche * (1 + _AUTOCOMPLETE_BREADTH) + settings.max_tool_calls_per_run


def seranking_credits_needed() -> int:
    questions = settings.seeds_per_niche * settings.questions_limit_per_seed * 10
    related = settings.seeds_per_niche * settings.related_limit_per_seed * 10
    return 100 + questions + related


class ProviderBalance(BaseModel):
    provider: str
    ok: bool
    remaining: Optional[float] = None
    required: Optional[float] = None
    unit: str = "credits"
    detail: Optional[str] = None  # why it failed, or a note worth surfacing
    checked: bool = True  # False when the balance endpoint itself was unreachable


def _scrappa_balance() -> ProviderBalance:
    need = scrappa_credits_needed()
    try:
        r = httpx.get(
            "https://scrappa.co/api/account/usage",
            headers={"x-api-key": settings.scrappa_api_key},
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()
        usable = (body.get("credits") or {}).get("usable")
        if usable is None:
            usable = body.get("balance")
        usable = float(usable or 0)
        return ProviderBalance(
            provider="scrappa", ok=usable >= need, remaining=usable, required=need,
            detail=None if usable >= need else f"{usable:,.0f} credits left, run needs ~{need:,}",
        )
    except Exception as e:  # noqa: BLE001 - an unreachable check must not block a run
        return ProviderBalance(
            provider="scrappa", ok=True, required=need, checked=False,
            detail=f"balance check unavailable: {e}",
        )


def _seranking_balance() -> ProviderBalance:
    need = seranking_credits_needed()
    try:
        r = httpx.get(
            "https://api.seranking.com/v1/account/credits",
            headers={"Authorization": f"Token {settings.seranking_api_key}"},
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()

        # The account can hold credits in three buckets at once (subscription,
        # add-on, wallet) and any of them can fund a call, so sum them rather
        # than reading one.
        totals = body.get("totals") or {}
        remaining = sum(
            float((totals.get(b) or {}).get("remaining") or 0)
            for b in ("subscription", "addon", "wallet")
        )

        # SE Ranking gates the Data API separately from the balance - credits
        # can exist while access is revoked, and spending against a revoked
        # key just fails.
        access = body.get("access") or {}
        can_use = bool(access.get("can_use_data_api", True))
        if not can_use:
            reason = access.get("primary_reason") or "access denied"
            return ProviderBalance(
                provider="seranking", ok=False, remaining=remaining, required=need,
                detail=f"Data API access is disabled ({reason})",
            )

        note = None
        expires = body.get("credit_expire_at")
        if expires:
            note = f"credits expire {expires}"
        if remaining < need:
            note = f"{remaining:,.0f} credits left, run needs ~{need:,}"
        return ProviderBalance(
            provider="seranking", ok=remaining >= need, remaining=remaining,
            required=need, detail=note,
        )
    except Exception as e:  # noqa: BLE001
        return ProviderBalance(
            provider="seranking", ok=True, required=need, checked=False,
            detail=f"balance check unavailable: {e}",
        )


def _deepseek_balance() -> ProviderBalance:
    try:
        r = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()
        infos = body.get("balance_infos") or []
        usd = float(next((b.get("total_balance") for b in infos if b.get("currency") == "USD"), 0) or 0)
        available = bool(body.get("is_available", True))
        ok = available and usd >= MIN_DEEPSEEK_USD
        return ProviderBalance(
            provider="deepseek", ok=ok, remaining=usd, required=MIN_DEEPSEEK_USD, unit="USD",
            detail=None if ok else (
                "account is not available for requests" if not available
                else f"${usd:.2f} left, below the ${MIN_DEEPSEEK_USD:.2f} floor"
            ),
        )
    except Exception as e:  # noqa: BLE001
        return ProviderBalance(
            provider="deepseek", ok=True, required=MIN_DEEPSEEK_USD, unit="USD",
            checked=False, detail=f"balance check unavailable: {e}",
        )


def check_balances() -> list[ProviderBalance]:
    """Read every provider's balance. Free - no credits are consumed."""
    return [_seranking_balance(), _scrappa_balance(), _deepseek_balance()]


def preflight() -> dict:
    """Whether a run should start, and the evidence either way.

    A provider whose balance endpoint is unreachable is treated as OK. Blocking
    every run because a status endpoint had a bad minute would be a worse
    failure than the one this guard exists to prevent - and the run's own
    4xx handling still fails loudly if the credits really are gone.
    """
    balances = check_balances()
    blocked = [b for b in balances if not b.ok]
    return {
        "ok": not blocked,
        "blocked_by": [b.provider for b in blocked],
        "reason": "; ".join(f"{b.provider}: {b.detail}" for b in blocked) or None,
        "providers": [b.model_dump() for b in balances],
    }
