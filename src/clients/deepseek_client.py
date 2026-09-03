from typing import Optional
import json
import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from src.config import settings
from src.clients.usage import UsageTracker

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class BudgetExceeded(Exception):
    pass


class DeepSeekClient:
    """Thin wrapper enforcing structured output + a hard call budget per run."""

    def __init__(self, max_calls: int = settings.max_tool_calls_per_run,
                 usage: Optional[UsageTracker] = None):
        self.max_calls = max_calls
        self.calls_made = 0
        self.usage = usage

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_not_exception_type(BudgetExceeded),
        # Without this tenacity raises RetryError instead of the original
        # exception, and every `except httpx.*` handler downstream misses it.
        reraise=True,
    )
    def _call(self, messages: list[dict]) -> str:
        if self.calls_made >= self.max_calls:
            raise BudgetExceeded(f"Hit max_calls={self.max_calls} for this run")
        self.calls_made += 1

        resp = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        if self.usage:
            # DeepSeek bills by token, not credits, so credits stays 0 here.
            total = (body.get("usage") or {}).get("total_tokens", 0)
            self.usage.record("deepseek", "chat/completions", tokens=total)
        return body["choices"][0]["message"]["content"]

    def structured_call(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> BaseModel:
        """Call DeepSeek and validate the response against a Pydantic schema.
        Retries once on schema validation failure with the error fed back in.

        The schema is injected into the system prompt because DeepSeek's JSON
        mode only guarantees *valid JSON*, not a particular shape - without
        this the model invents its own field names. That failed silently for
        any schema with defaulted fields (the model returned
        {"kept_keywords": [...]}, Pydantic filled the expected field with its
        default, and the caller saw an empty result rather than an error)."""
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        messages = [
            {
                "role": "system",
                "content": f"{system_prompt}\n\nRespond with JSON conforming to this "
                f"JSON Schema. Use exactly these field names:\n{schema_json}",
            },
            {"role": "user", "content": user_prompt},
        ]
        raw = self._call(messages)
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
            raw_retry = self._call(messages)
            return schema.model_validate_json(raw_retry)
