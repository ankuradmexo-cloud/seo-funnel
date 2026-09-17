from typing import Optional
import json
import httpx
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekClient:
    """Two call modes: structured_call for extraction/analysis steps that
    must return a specific shape, generate for free-form prose (the writer).

    structured_call injects the JSON Schema into the prompt and validates the
    response against it - a lesson carried over from the keyword funnel
    project, where DeepSeek's JSON mode (valid JSON, but not necessarily the
    right shape) silently dropped every candidate in a batch because the
    model used different field names than the schema expected, and a
    defaulted field swallowed the mismatch instead of raising."""

    def __init__(self):
        self.total_tokens = 0
        self.calls_made = 0
        self.truncation_retries = 0
        # One entry per actual API call (a schema-validation retry inside
        # structured_call counts as its own entry, not folded into the
        # first) - prompt/completion split and cache-hit/miss come straight
        # from DeepSeek's usage object, not estimated. See
        # pipeline/cost_tracking.py for how this gets turned into a dollar
        # figure.
        self.call_log: list[dict] = []

    def _record_usage(self, label: str, body: dict) -> None:
        usage = body.get("usage") or {}
        entry = {
            "label": label,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            # DeepSeek's disk-cache mechanism - present as separate fields
            # when populated, absent (defaults to 0) otherwise. Cache-hit
            # tokens are billed at a lower rate than cache-miss ones, but
            # this client doesn't know this account's exact cache-hit
            # price, so cost_tracking.py prices all prompt_tokens at the
            # cache-miss rate - conservative (an overestimate, not an
            # underestimate) rather than guessing at a discount.
            "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
            "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
        }
        self.call_log.append(entry)
        self.total_tokens += entry["total_tokens"]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _call_raw(
        self, messages: list[dict], temperature: float = 0.3,
        max_tokens: Optional[int] = None, json_mode: bool = False, label: str = "unlabeled",
    ) -> dict:
        """Returns the full response body, not just the text - callers that
        need to know *why* a generation ended (finish_reason) use this
        directly; generate()/structured_call() extract just the content."""
        self.calls_made += 1
        payload = {"model": "deepseek-chat", "messages": messages, "temperature": temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json=payload,
            timeout=180,
        )
        resp.raise_for_status()
        body = resp.json()
        self._record_usage(label, body)
        return body

    def _call(
        self, messages: list[dict], temperature: float = 0.3,
        max_tokens: Optional[int] = None, json_mode: bool = False, label: str = "unlabeled",
    ) -> str:
        body = self._call_raw(
            messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode, label=label,
        )
        return body["choices"][0]["message"]["content"]

    def generate(
        self, system_prompt: str, user_prompt: str,
        temperature: float = 0.4, max_tokens: Optional[int] = None, label: str = "unlabeled",
    ) -> str:
        """Free-form text generation - used for section drafting, not extraction."""
        return self._call(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=temperature, max_tokens=max_tokens, label=label,
        )

    def generate_uncapped_if_truncated(
        self, system_prompt: str, user_prompt: str,
        temperature: float = 0.4, max_tokens: Optional[int] = None, label: str = "unlabeled",
    ) -> str:
        """Like generate(), but a response cut off by hitting max_tokens
        (finish_reason == "length") is retried once with the ceiling doubled
        instead of silently shipping a mid-sentence truncation.

        Important: this DISCARDS the truncated draft and regenerates from
        scratch at the doubled ceiling - it does not continue the draft. That
        makes it a poor length control on its own: it only ever fires on
        sections that were already going to run long, and hands exactly
        those sections double the room. Measured directly - with
        pipeline/article_writer.py's old 2.0x cap, this fired on 6 of 11
        sections and those were the 6 worst overshoots (+75% to +171%),
        while the 5 sections that stayed under the cap ran +16% to +45%.
        The actual length control is pipeline/length_control.py, which
        measures and condenses AFTER a complete draft exists; max_tokens
        here exists only to stop truly runaway generation, sized with
        headroom precisely so this branch should rarely trigger."""
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        body = self._call_raw(messages, temperature=temperature, max_tokens=max_tokens, label=label)
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length" and max_tokens:
            self.truncation_retries += 1
            body = self._call_raw(
                messages, temperature=temperature, max_tokens=max_tokens * 2, label=f"{label}:retry_uncapped",
            )
            choice = body["choices"][0]
        return choice["message"]["content"]

    def structured_call(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel], label: str = "unlabeled",
    ) -> BaseModel:
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        messages = [
            {
                "role": "system",
                "content": f"{system_prompt}\n\nRespond with JSON conforming to this "
                f"JSON Schema. Use exactly these field names:\n{schema_json}",
            },
            {"role": "user", "content": user_prompt},
        ]
        raw = self._call(messages, temperature=0.2, json_mode=True, label=label)
        try:
            return schema.model_validate_json(raw)
        except Exception as e:
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": f"Your response failed schema validation: {e}. "
                    f"Return ONLY valid JSON matching the required schema.",
                }
            )
            raw_retry = self._call(messages, temperature=0.2, json_mode=True, label=f"{label}:schema_retry")
            return schema.model_validate_json(raw_retry)
