"""Turns a completed run's per-call token log (DeepSeekClient.call_log) plus
the other APIs' known flat rates into an actual dollar figure and a
per-label token breakdown - the real, measured answer to "what does one
article cost".

Pricing sources:
- DeepSeek: NOT taken from DeepSeek's published pricing page - that was
  tried twice (first assuming deepseek-flash rates, then deepseek-v4-pro
  rates) and both were wrong, the second one by ~6.4x. This account's
  actual DeepSeek balance was checked before and after a real 4-article
  batch run ($3.60 -> $3.40, $0.20 total) and the DeepSeek portion
  isolated by subtracting SE Ranking's known flat costs ($0.1964 ->
  $0.1164 for DeepSeek alone across 880,760 prompt / 91,132 completion /
  31,104 cache-hit tokens). These constants are that real number divided
  back across prompt/cache-hit/completion tokens in the same 30x-cache-
  discount SHAPE the docs described, scaled down until the total matches -
  a calibration against one observed data point, not a confirmed rate
  card. If DeepSeek's dashboard shows an exact per-token rate for this
  account, use that instead and delete this whole approximation.
- SE Ranking: $50 = 250,000 credits; keywords/export is a flat 100 credits
  regardless of batch size, serp/classic tasks are a flat 50 credits/task
  (both measured directly against the account's own credit counter).
"""

DEEPSEEK_CACHE_HIT_INPUT_PER_MILLION = 0.0034
DEEPSEEK_CACHE_MISS_INPUT_PER_MILLION = 0.1035
DEEPSEEK_OUTPUT_PER_MILLION = 0.3105

SERANKING_COST_PER_CREDIT = 50 / 250_000


def _deepseek_call_cost(prompt_tokens: int, completion_tokens: int, cache_hit_tokens: int) -> float:
    """cache_hit_tokens is clamped to prompt_tokens - a call with no
    prompt_cache_hit_tokens field reported (0) is priced entirely at the
    cache-miss rate, which is the correct/conservative default, not an
    approximation."""
    cache_hit_tokens = min(cache_hit_tokens, prompt_tokens)
    cache_miss_tokens = prompt_tokens - cache_hit_tokens
    return (
        cache_hit_tokens / 1_000_000 * DEEPSEEK_CACHE_HIT_INPUT_PER_MILLION
        + cache_miss_tokens / 1_000_000 * DEEPSEEK_CACHE_MISS_INPUT_PER_MILLION
        + completion_tokens / 1_000_000 * DEEPSEEK_OUTPUT_PER_MILLION
    )


def compute_run_cost(deepseek_call_log: list[dict], seranking_credits_used: int) -> dict:
    """deepseek_call_log is DeepSeekClient.call_log after a run - one entry
    per actual API call (schema-validation retries and truncation retries
    each count as their own entry, since they're separately-billed calls).
    Returns a dict with the total cost, a per-provider breakdown, and a
    per-label DeepSeek breakdown so it's visible which steps actually drive
    spend."""
    by_label: dict[str, dict] = {}
    deepseek_prompt_tokens = 0
    deepseek_completion_tokens = 0
    deepseek_cache_hit_tokens = 0
    deepseek_cost = 0.0

    for entry in deepseek_call_log:
        label = entry["label"]
        bucket = by_label.setdefault(
            label,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0, "cost": 0.0},
        )
        prompt_tokens = entry["prompt_tokens"]
        completion_tokens = entry["completion_tokens"]
        cache_hit_tokens = entry.get("prompt_cache_hit_tokens", 0)
        call_cost = _deepseek_call_cost(prompt_tokens, completion_tokens, cache_hit_tokens)

        bucket["calls"] += 1
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["cache_hit_tokens"] += cache_hit_tokens
        bucket["cost"] += call_cost

        deepseek_prompt_tokens += prompt_tokens
        deepseek_completion_tokens += completion_tokens
        deepseek_cache_hit_tokens += cache_hit_tokens
        deepseek_cost += call_cost

    seranking_cost = seranking_credits_used * SERANKING_COST_PER_CREDIT

    by_label_rounded = {
        label: {
            "calls": b["calls"],
            "prompt_tokens": b["prompt_tokens"],
            "completion_tokens": b["completion_tokens"],
            "total_tokens": b["prompt_tokens"] + b["completion_tokens"],
            "cache_hit_tokens": b["cache_hit_tokens"],
            "cost_usd": round(b["cost"], 5),
        }
        for label, b in sorted(by_label.items(), key=lambda kv: -kv[1]["cost"])
    }

    return {
        "total_cost_usd": round(deepseek_cost + seranking_cost, 5),
        "deepseek": {
            "calls": len(deepseek_call_log),
            "prompt_tokens": deepseek_prompt_tokens,
            "completion_tokens": deepseek_completion_tokens,
            "total_tokens": deepseek_prompt_tokens + deepseek_completion_tokens,
            "cache_hit_tokens": deepseek_cache_hit_tokens,
            "cost_usd": round(deepseek_cost, 5),
        },
        "seranking": {"credits": seranking_credits_used, "cost_usd": round(seranking_cost, 5)},
        "deepseek_by_label": by_label_rounded,
    }
