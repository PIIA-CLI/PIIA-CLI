"""A provider-agnostic chat client.

Two wire formats cover essentially every endpoint a user will point this at:

``openai`` (default)
    ``POST {base_url}/chat/completions``. Works with OpenAI, Azure OpenAI,
    LiteLLM, vLLM, llama.cpp, Ollama's ``/v1``, LM Studio, OpenRouter,
    Together, Fireworks, Groq, DeepSeek, Mistral, and Google's
    OpenAI-compatible Gemini endpoint.

``anthropic``
    ``POST {base_url}/v1/messages`` with ``x-api-key`` and
    ``anthropic-version``.

The client is deliberately thin -- no provider SDKs, one HTTP dependency -- so
that adding a provider is a configuration change, not a code change.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from piia.config import LLMSettings
from piia.errors import LLMError, LLMResponseError

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class ChatResponse:
    """One model reply plus the accounting a legal audit trail needs."""

    text: str
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    attempts: int = 1
    finish_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "finish_reason": self.finish_reason,
            "characters": len(self.text),
        }


@dataclass
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class ChatClient:
    """Minimal chat client with retries, usage accounting and JSON coercion."""

    def __init__(self, settings: LLMSettings, *, transport: httpx.BaseTransport | None = None):
        settings.require()
        self.settings = settings
        self._transport = transport
        self._client: httpx.Client | None = None
        self.total_usage: dict[str, int] = {}
        self.call_count = 0

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> ChatClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.settings.timeout),
                verify=self.settings.verify_ssl,
                transport=self._transport,
                follow_redirects=True,
            )
        return self._client

    # -- request shaping ---------------------------------------------------
    @property
    def endpoint(self) -> str:
        base = (self.settings.base_url or "").rstrip("/")
        if self.settings.api_style == "anthropic":
            if base.endswith("/messages"):
                return base
            if base.endswith("/v1"):
                return f"{base}/messages"
            return f"{base}/v1/messages"
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = self.settings.api_key
        if self.settings.api_style == "anthropic":
            headers["anthropic-version"] = self.settings.api_version or DEFAULT_ANTHROPIC_VERSION
            if key:
                headers["x-api-key"] = key
        elif key:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(self.settings.extra_headers)
        return headers

    def _body(self, messages: list[Message], *, json_mode: bool) -> dict[str, Any]:
        s = self.settings
        system = [m for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        if s.api_style == "anthropic":
            body: dict[str, Any] = {
                "model": s.model,
                "max_tokens": s.max_tokens or 8192,
                "temperature": s.temperature,
                "messages": [m.to_dict() for m in rest],
            }
            if system:
                body["system"] = "\n\n".join(m.content for m in system)
            if s.top_p is not None:
                body["top_p"] = s.top_p
            return body

        body = {
            "model": s.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": s.temperature,
            "stream": False,
        }
        if s.max_tokens:
            body["max_tokens"] = s.max_tokens
        if s.top_p is not None:
            body["top_p"] = s.top_p
        if s.seed is not None:
            body["seed"] = s.seed
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    # -- calling -----------------------------------------------------------
    def complete(
        self,
        messages: list[Message],
        *,
        json_mode: bool = False,
    ) -> ChatResponse:
        """Send one chat request, retrying transient failures with backoff."""
        started = time.perf_counter()
        attempts = 0
        last_error: Exception | None = None
        allow_json_mode = json_mode

        while attempts < max(1, self.settings.max_retries):
            attempts += 1
            body = self._body(messages, json_mode=allow_json_mode)
            try:
                response = self.client.post(self.endpoint, json=body, headers=self.headers())
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                self._sleep(attempts)
                continue

            if response.status_code == 400 and allow_json_mode and "response_format" in response.text:
                # The endpoint rejects structured-output mode; fall back to
                # prompt-enforced JSON, which every provider supports.
                allow_json_mode = False
                attempts -= 1
                continue

            if response.status_code in RETRY_STATUS:
                last_error = LLMError(
                    f"Endpoint returned HTTP {response.status_code}.",
                    details=[{"field": "body", "issue": response.text[:400]}],
                )
                self._sleep(attempts, retry_after=response.headers.get("retry-after"))
                continue

            if response.status_code >= 400:
                raise LLMError(
                    f"Model request failed with HTTP {response.status_code}.",
                    details=[
                        {"field": "endpoint", "issue": self.endpoint},
                        {"field": "body", "issue": response.text[:600]},
                    ],
                    remediation=_http_remediation(response.status_code),
                )

            self.call_count += 1
            parsed = self._parse(response)
            parsed.latency_ms = int((time.perf_counter() - started) * 1000)
            parsed.attempts = attempts
            for key, value in parsed.usage.items():
                if isinstance(value, int):
                    self.total_usage[key] = self.total_usage.get(key, 0) + value
            return parsed

        raise LLMError(
            f"Model request failed after {attempts} attempt(s): {last_error}",
            details=[{"field": "endpoint", "issue": self.endpoint}],
            remediation=(
                "Verify LLM_BASE_URL is reachable from this machine "
                "('piia doctor' checks it), raise LLM_TIMEOUT, or pass --no-llm to "
                "generate a deterministic draft without a model."
            ),
        )

    def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                time.sleep(min(30.0, float(retry_after)))
                return
            except ValueError:
                pass
        # Full jitter, capped -- a legal drafting run should not hammer a
        # rate-limited endpoint.
        time.sleep(min(20.0, (2 ** (attempt - 1)) * random.uniform(0.4, 1.2)))

    def _parse(self, response: httpx.Response) -> ChatResponse:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMResponseError(
                "The endpoint did not return JSON.",
                details=[{"field": "body", "issue": response.text[:400]}],
                remediation="Check that LLM_BASE_URL points at an API root, not a web UI.",
            ) from exc

        if self.settings.api_style == "anthropic":
            blocks = payload.get("content") or []
            text = "".join(
                b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )
            return ChatResponse(
                text=text,
                model=payload.get("model"),
                usage=payload.get("usage") or {},
                finish_reason=payload.get("stop_reason"),
            )

        choices = payload.get("choices") or []
        if not choices:
            raise LLMResponseError(
                "The endpoint returned no choices.",
                details=[{"field": "body", "issue": json.dumps(payload)[:400]}],
                remediation="Confirm LLM_MODEL_NAME names a model this endpoint serves.",
            )
        message = choices[0].get("message") or {}
        text = message.get("content")
        if isinstance(text, list):  # some gateways return content parts
            text = "".join(
                part.get("text", "") for part in text if isinstance(part, dict)
            )
        return ChatResponse(
            text=text or "",
            model=payload.get("model"),
            usage=payload.get("usage") or {},
            finish_reason=choices[0].get("finish_reason"),
        )

    # -- structured output -------------------------------------------------
    def complete_json(
        self,
        messages: list[Message],
        *,
        required_keys: tuple[str, ...] = (),
        max_repairs: int = 2,
    ) -> tuple[dict[str, Any], ChatResponse]:
        """Get a JSON object back, repairing malformed replies up to twice.

        Local and small models frequently wrap JSON in prose or fences, so this
        extracts the first balanced object before giving up, and only then asks
        the model to try again.
        """
        conversation = list(messages)
        last: ChatResponse | None = None
        for attempt in range(max_repairs + 1):
            response = self.complete(conversation, json_mode=True)
            last = response
            data = extract_json(response.text)
            if data is not None and all(k in data for k in required_keys):
                return data, response
            if attempt == max_repairs:
                break
            problem = (
                "the reply was not a JSON object"
                if data is None
                else "these required keys were missing: "
                + ", ".join(k for k in required_keys if k not in data)
            )
            conversation = [
                *messages,
                Message(role="assistant", content=response.text[:4000]),
                Message(
                    role="user",
                    content=(
                        f"That response could not be used: {problem}. "
                        "Reply again with a single valid JSON object and nothing else -- "
                        "no prose, no markdown fences."
                    ),
                ),
            ]
        raise LLMResponseError(
            "The model did not return usable JSON after repair attempts.",
            details=[
                {"field": "required_keys", "issue": ", ".join(required_keys) or "(none)"},
                {"field": "last_reply", "issue": (last.text[:400] if last else "")},
            ],
            remediation=(
                "Try a larger or instruction-tuned model, lower LLM_TEMPERATURE, "
                "or run with --no-llm for a deterministic draft."
            ),
        )


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply.

    Handles bare JSON, ```json fences, and prose wrapped around an object.
    """
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = [ln for ln in candidate.splitlines() if not ln.strip().startswith("```")]
        candidate = "\n".join(lines).strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(candidate)):
            char = candidate[idx]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(candidate[start : idx + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
        start = candidate.find("{", start + 1)
    return None


def _http_remediation(status: int) -> str:
    if status in (401, 403):
        return (
            "Set LLM_API_KEY (or clear it if the endpoint takes no key) and confirm the key "
            "is valid for this endpoint."
        )
    if status == 404:
        return (
            "Check LLM_BASE_URL and LLM_MODEL_NAME. Most OpenAI-compatible servers expect a "
            "base URL ending in '/v1'. Run 'piia doctor' to list what the endpoint serves."
        )
    if status == 413:
        return "Lower PIIA_MAX_FILES or split the prior-work list into smaller runs."
    if status == 422:
        return "The endpoint rejected a parameter; try clearing LLM_TOP_P and LLM_SEED."
    return "Run 'piia doctor' to test the endpoint, or pass --no-llm to skip model drafting."
