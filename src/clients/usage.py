"""Per-run API usage accounting.

Credits are accumulated in memory during a run and written once at the end,
rather than one row per call - a single run makes ~330 Scrappa autocomplete
requests, and inserting a row per request would add hundreds of DB round trips
to an already slow pipeline.

Credit costs are provider-specific and not interchangeable:
  Scrappa      1 credit per request, regardless of how many results come back
  SE Ranking   100 credits flat for keywords/export (any batch size up to 5000)
               10 credits per RETURNED keyword for questions/related
  DeepSeek     billed by token, not credits - tracked as calls/tokens instead
"""

SCRAPPA_CREDITS_PER_CALL = 1
SERANKING_EXPORT_CREDITS = 100
SERANKING_CREDITS_PER_RETURNED_KEYWORD = 10


class UsageTracker:
    def __init__(self):
        # (provider, endpoint) -> {"calls": int, "credits": float, "tokens": int}
        self._rows: dict[tuple[str, str], dict[str, float]] = {}

    def record(self, provider: str, endpoint: str, credits: float = 0.0,
               calls: int = 1, tokens: int = 0) -> None:
        row = self._rows.setdefault(
            (provider, endpoint), {"calls": 0, "credits": 0.0, "tokens": 0}
        )
        row["calls"] += calls
        row["credits"] += credits
        row["tokens"] += tokens

    def as_rows(self, run_id: int, website_id: int) -> list[dict]:
        return [
            {
                "run_id": run_id,
                "website_id": website_id,
                "provider": provider,
                "endpoint": endpoint,
                "calls": int(v["calls"]),
                "credits": round(v["credits"], 2),
                "tokens": int(v["tokens"]),
            }
            for (provider, endpoint), v in self._rows.items()
        ]

    def summary(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for (provider, _), v in self._rows.items():
            agg = out.setdefault(provider, {"calls": 0, "credits": 0.0, "tokens": 0})
            agg["calls"] += v["calls"]
            agg["credits"] += v["credits"]
            agg["tokens"] += v["tokens"]
        return out
