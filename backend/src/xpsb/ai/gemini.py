"""Google Gemini (Generative Language API, REST) with the same interface as the
Claude transport: system prompt, text and image blocks, a JSON schema for the
answer, token counts for the price. The key lives only in this object, never in
logs or files; it is sent as a header, never in the URL.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from xpsb.ai.analyze import DEFAULT_MAX_TOKENS, AiAuthError, AiCallError, AiResponse
from xpsb.ai.providers import is_gemini_25

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta"
RETRIES = 3  # busy (429) or server errors: wait and try again
MAX_OUTPUT_TOKENS = 65536
# Thinking per effort: Gemini 3 knows levels, 2.5 a token budget.
LEVELS = {"low": "low", "medium": "high", "high": "high"}
BUDGETS = {"low": 1024, "medium": 4096, "high": 8192}
REFUSALS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
# Not for building scenery (and not offered when listing the user's models).
NOT_FOR_US = ("embedding", "tts", "image", "live", "audio", "robotics", "computer-use", "aqa")


def _inline_refs(schema: dict) -> dict:
    """The pydantic schema with $refs replaced by their definitions (Gemini reads
    plain nested schemas most reliably) and without titles/defaults."""
    defs = schema.get("$defs", {})

    def walk(node: Any, depth: int = 0) -> Any:
        if depth > 30:
            raise ValueError("schema too deep")
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return walk(copy.deepcopy(defs[name]), depth + 1)
            return {
                k: walk(v, depth + 1)
                for k, v in node.items()
                if k not in ("$defs", "title", "default", "additionalProperties")
            }
        if isinstance(node, list):
            return [walk(v, depth + 1) for v in node]
        return node

    return walk(schema)


def _parts(content: list[dict]) -> list[dict]:
    """Claude-style content blocks as Gemini parts."""
    parts = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            parts.append({"text": block["text"]})
        elif kind == "image":
            src = block["source"]
            parts.append({"inlineData": {"mimeType": src["media_type"], "data": src["data"]}})
    return parts


class GeminiTransport:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._key = api_key
        # The longest pause between bytes of the answer (long build scripts think).
        self._client = client or httpx.Client(timeout=httpx.Timeout(600.0, connect=20.0))

    def _post(self, model: str, body: dict) -> dict:
        url = f"{API}/models/{model}:generateContent"
        for attempt in range(RETRIES + 1):
            try:
                r = self._client.post(url, json=body, headers={"x-goog-api-key": self._key})
            except httpx.HTTPError as exc:
                if attempt < RETRIES:
                    time.sleep(2 * 2**attempt)
                    continue
                raise AiCallError(type(exc).__name__) from None
            if r.status_code == 200:
                return r.json()
            message = _error_message(r)
            if r.status_code in (401, 403) or "API_KEY_INVALID" in r.text:
                raise AiAuthError(f"HTTP {r.status_code}")
            if r.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                log.warning("gemini HTTP %s, retrying", r.status_code)
                time.sleep(2 * 2**attempt)
                continue
            if r.status_code == 404:
                raise AiCallError(f"HTTP 404 {message}"[:300], key="error.ai_model_unavailable")
            raise AiCallError(f"HTTP {r.status_code} {message}"[:300])
        raise AiCallError("no answer")  # not reached

    def __call__(
        self,
        *,
        model: str,
        system: str,
        content: list[dict],
        output_format: type[BaseModel],
        effort: str = "low",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AiResponse:
        if is_gemini_25(model):
            thinking: dict[str, Any] = {"thinkingBudget": BUDGETS.get(effort, 1024)}
            room = thinking["thinkingBudget"]
        else:
            thinking = {"thinkingLevel": LEVELS.get(effort, "low")}
            room = 8192 if thinking["thinkingLevel"] == "high" else 2048
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": _parts(content)}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": _inline_refs(output_format.model_json_schema()),
                # Thinking counts against the output limit: room for it on top.
                "maxOutputTokens": min(MAX_OUTPUT_TOKENS, max_tokens + room),
                "thinkingConfig": thinking,
            },
        }
        data = self._post(model, body)
        usage = data.get("usageMetadata") or {}
        prompt = int(usage.get("promptTokenCount") or 0)
        cached = int(usage.get("cachedContentTokenCount") or 0)
        output = int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )
        candidates = data.get("candidates") or []
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        finish = str(candidates[0].get("finishReason") or "") if candidates else ""
        refused = bool(blocked) or finish in REFUSALS or not candidates
        stop = "refusal" if refused else "max_tokens" if finish == "MAX_TOKENS" else "end_turn"
        parsed = None
        if stop == "end_turn":
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            try:
                parsed = output_format.model_validate_json(text) if text else None
            except ValueError:
                parsed = None
        return AiResponse(
            parsed=parsed,
            model=str(data.get("modelVersion") or model),
            input_tokens=max(0, prompt - cached),
            output_tokens=output,
            cache_read_tokens=cached,  # Gemini caches repeated prefixes by itself
            refused=refused,
            stop_reason=stop,
        )


def _error_message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", {}).get("message", ""))
    except ValueError:
        return ""


def list_models(api_key: str, client: httpx.Client | None = None) -> list[str]:
    """The Gemini models this key may use for text and images (newest names first)."""
    client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
    out: list[str] = []
    token = ""
    for _ in range(10):  # pages
        params = {"pageSize": "200", **({"pageToken": token} if token else {})}
        r = client.get(f"{API}/models", params=params, headers={"x-goog-api-key": api_key})
        if r.status_code in (400, 401, 403):
            raise AiAuthError(f"HTTP {r.status_code}")
        if r.status_code != 200:
            raise AiCallError(f"HTTP {r.status_code} {_error_message(r)}"[:300])
        data = r.json()
        for m in data.get("models", []):
            name = str(m.get("name", "")).removeprefix("models/")
            if not name.startswith("gemini-") or any(w in name for w in NOT_FOR_US):
                continue
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                out.append(name)
        token = data.get("nextPageToken") or ""
        if not token:
            break
    return sorted(set(out), reverse=True)
