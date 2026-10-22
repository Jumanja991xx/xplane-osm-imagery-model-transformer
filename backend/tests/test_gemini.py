"""Gemini as second AI provider: the request Google gets, the answer we read, the
price, the key per provider and the choice of the cheaper model."""

from __future__ import annotations

import json

import httpx
import pytest

from xpsb.ai import analyze as ai
from xpsb.ai import gemini, providers, usage
from xpsb.jobs.models import Brief, scan_model
from xpsb.secrets import ANTHROPIC_ACCOUNT, GEMINI_ACCOUNT
from xpsb.settings import Settings

PNG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _answer(text: str, finish: str = "STOP", **usage_extra) -> dict:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": text}]},
                "finishReason": finish,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 1200,
            "cachedContentTokenCount": 1000,
            "candidatesTokenCount": 300,
            "thoughtsTokenCount": 500,
            **usage_extra,
        },
        "modelVersion": "gemini-3.8-flash-001",
    }


def test_request_and_answer(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        items = {"items": []}
        return httpx.Response(200, json=_answer(json.dumps(items)))

    t = gemini.GeminiTransport("secret-key", client=_client(handler))
    res = t(
        model="gemini-3.8-flash",
        system="be helpful",
        content=[{"type": "text", "text": "look", "cache_control": {"type": "ephemeral"}}, PNG],
        output_format=ai.AiBatchResult,
        effort="medium",
        max_tokens=4000,
    )
    assert seen["url"].endswith("/models/gemini-3.8-flash:generateContent")
    assert "secret-key" not in seen["url"] and seen["key"] == "secret-key"
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "be helpful"
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "look"}
    assert parts[1] == {"inlineData": {"mimeType": "image/png", "data": "QUJD"}}
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert "$ref" not in json.dumps(cfg["responseJsonSchema"])
    assert cfg["thinkingConfig"] == {"thinkingLevel": "high"}
    assert cfg["maxOutputTokens"] > 4000  # room for thinking
    # the thought part is not the answer; cached tokens are not billed as input
    assert isinstance(res.parsed, ai.AiBatchResult)
    assert (res.input_tokens, res.cache_read_tokens, res.output_tokens) == (200, 1000, 800)
    assert res.model == "gemini-3.8-flash-001" and res.stop_reason == "end_turn"


def test_gemini_25_thinks_with_a_budget():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_answer('{"items": []}'))

    gemini.GeminiTransport("k", client=_client(handler))(
        model="gemini-2.5-flash",
        system="s",
        content=[],
        output_format=ai.AiBatchResult,
        effort="low",
    )
    assert seen["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 1024}


@pytest.mark.parametrize(
    ("finish", "stop", "refused"),
    [("MAX_TOKENS", "max_tokens", False), ("SAFETY", "refusal", True)],
)
def test_cut_off_and_declined_answers(finish, stop, refused):
    t = gemini.GeminiTransport(
        "k", client=_client(lambda r: httpx.Response(200, json=_answer("{", finish)))
    )
    res = t(model="gemini-3.8-flash", system="s", content=[], output_format=ai.AiBatchResult)
    assert (res.stop_reason, res.refused, res.parsed) == (stop, refused, None)


def test_a_wrong_key_and_a_busy_server(monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    bad = httpx.Response(
        400,
        json={
            "error": {"message": "API key not valid", "details": [{"reason": "API_KEY_INVALID"}]}
        },
    )
    t = gemini.GeminiTransport("k", client=_client(lambda r: bad))
    with pytest.raises(ai.AiAuthError):
        t(model="gemini-3.8-flash", system="s", content=[], output_format=ai.AiBatchResult)

    calls = []

    def busy_then_ok(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=_answer('{"items": []}'))

    t = gemini.GeminiTransport("k", client=_client(busy_then_ok))
    res = t(model="gemini-3.8-flash", system="s", content=[], output_format=ai.AiBatchResult)
    assert len(calls) == 3 and res.parsed is not None

    gone = httpx.Response(404, json={"error": {"message": "model not found"}})
    t = gemini.GeminiTransport("k", client=_client(lambda r: gone))
    with pytest.raises(ai.AiCallError) as err:
        t(model="gemini-9-x", system="s", content=[], output_format=ai.AiBatchResult)
    assert err.value.key == "error.ai_model_unavailable"


def test_listing_the_models_of_the_key():
    models = [
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/imagen-4", "supportedGenerationMethods": ["predict"]},
    ]
    client = _client(lambda r: httpx.Response(200, json={"models": models}))
    assert gemini.list_models("k", client) == ["gemini-3.8-flash", "gemini-2.5-flash"]


def test_prices_and_the_cheaper_model():
    assert usage.price("gemini-3.8-flash") == (1.50, 7.50)
    # a dated version of a known model costs the same; an unknown one the most
    assert usage.price("gemini-3.8-flash-001") == (1.50, 7.50)
    assert usage.price("gemini-4-ultra") == max(
        p for m, p in usage.PRICES.items() if m.startswith("gemini-")
    )
    # long prompts on Pro cost more
    short = usage.cost_usd("gemini-3.1-pro-preview", 100_000, 0)
    long = usage.cost_usd("gemini-3.1-pro-preview", 250_000, 0)
    assert long / 250_000 > short / 100_000

    def brief(model):
        return Brief(ai_model=model, package_name="Test", object_categories=["buildings"])

    assert scan_model(brief("gemini-3.1-pro-preview")) == "gemini-3.8-flash"
    assert scan_model(brief("gemini-2.5-pro")) == "gemini-2.5-flash"
    assert scan_model(brief("gemini-3.8-flash")) == "gemini-3.8-flash"
    assert scan_model(brief("claude-opus-5-5")) == "claude-sonnet-5-5"


def test_settings_accept_known_and_reported_models_only():
    Settings.model_validate({"ai": {"model": "gemini-3.8-flash"}})
    Settings.model_validate({"ai": {"model": "gemini-3.9-flash-preview-11-2026"}})
    with pytest.raises(ValueError):
        Settings.model_validate({"ai": {"model": "gpt-5"}})
    assert providers.key_account("gemini-3.8-flash") == GEMINI_ACCOUNT
    assert providers.key_account("claude-opus-5-5") == ANTHROPIC_ACCOUNT


def test_gemini_jobs_use_the_gemini_key(client, app_ctx, fake_ai):
    """A job with a Gemini model asks for the Gemini key and gets a transport for
    its model (economy: Flash for looking at the aerial image); no Claude key needed."""
    from conftest import AUTH
    from test_analysis import ALL, analyze, setup

    r = client.put("/api/secrets/gemini", json={"api_key": "gm-key"}, headers=AUTH)
    assert r.status_code == 200
    assert client.get("/api/system", headers=AUTH).json()["has_gemini_key"] is True
    job = setup(client, key=False, brief={**ALL, "ai_model": "gemini-3.1-pro-preview"})
    keys = [i["key"] for i in job["pending_gate"]["items"]]
    assert "gate.a.ai_no_key" not in keys
    job = analyze(client, job)
    assert fake_ai.keys == ["gm-key"] and fake_ai.transport_models == ["gemini-3.8-flash"]
    assert job["analysis"]["ai"]["analyzed"] > 0
    models = client.get("/api/ai/models", headers=AUTH).json()
    assert {m["provider"] for m in models} == {"anthropic", "google"}
