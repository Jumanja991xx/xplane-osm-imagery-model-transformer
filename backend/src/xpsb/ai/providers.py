"""The AI models the app offers and which provider (and key) each one needs.

Claude (Anthropic) and Gemini (Google): the user brings their own key for one or
both. Google renames and retires Gemini models often, so besides the list below any
model id Google reports for the user's key ("load available models") can be chosen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from xpsb.secrets import ANTHROPIC_ACCOUNT, GEMINI_ACCOUNT

Provider = Literal["anthropic", "google"]


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: Provider
    tier: Literal["top", "mid", "low"]  # quality / price class
    note: str = ""  # shown next to the name (preview, limited access ...)


MODELS: list[ModelInfo] = [
    ModelInfo("claude-opus-5-5", "anthropic", "top"),
    ModelInfo("claude-sonnet-5-5", "anthropic", "mid"),
    ModelInfo("claude-haiku-5-5", "anthropic", "low"),
    # Gemini, newest first (as of 2026-10).
    ModelInfo("gemini-3.1-pro-preview", "google", "top", "preview"),
    ModelInfo("gemini-3.8-flash", "google", "mid"),
    ModelInfo("gemini-3.6-flash", "google", "mid"),
    ModelInfo("gemini-3-flash-preview", "google", "mid", "preview"),
    ModelInfo("gemini-3.5-flash-lite", "google", "low"),
    ModelInfo("gemini-3.1-flash-lite", "google", "low"),
    # Google serves the 2.5 models only to accounts that used them before.
    ModelInfo("gemini-2.5-pro", "google", "top", "limited"),
    ModelInfo("gemini-2.5-flash", "google", "mid", "limited"),
]
KNOWN = {m.id: m for m in MODELS}
GEMINI_ID = re.compile(r"^gemini-[a-z0-9][a-z0-9.\-]{1,60}$")
CLAUDE_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.\-]{1,60}$")


def provider_of(model: str) -> Provider:
    return "google" if model.startswith("gemini-") else "anthropic"


def key_account(model: str) -> str:
    return GEMINI_ACCOUNT if provider_of(model) == "google" else ANTHROPIC_ACCOUNT


def valid_model(model: str) -> bool:
    """A model we know, or a Gemini model id as Google reports it."""
    return model in KNOWN or bool(GEMINI_ID.match(model))


def check_model(model: str) -> str:
    if not valid_model(model):
        raise ValueError(f"unknown AI model: {model}")
    return model


def economy_model(model: str) -> str | None:
    """A cheaper model of the same provider for looking at the aerial image (search,
    rating), or None to keep the chosen one."""
    if "opus" in model:
        return "claude-sonnet-5-5"
    if model.startswith("gemini-2.5-pro"):
        return "gemini-2.5-flash"  # same family: whoever has 2.5 Pro has 2.5 Flash
    if model.startswith("gemini-") and "-pro" in model:
        return "gemini-3.8-flash"
    return None


def is_gemini_25(model: str) -> bool:
    return model.startswith("gemini-2.")


def make_transport(api_key: str, model: str = ""):
    """The transport for the model's provider (imported lazily: the SDKs are big)."""
    if provider_of(model) == "google":
        from xpsb.ai.gemini import GeminiTransport

        return GeminiTransport(api_key)
    from xpsb.ai.analyze import AnthropicTransport

    return AnthropicTransport(api_key)
