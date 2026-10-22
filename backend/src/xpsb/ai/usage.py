"""Prices and a small ledger of what the AI cost, so monthly limits hold across jobs."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

# List prices (USD per million tokens: input, output), as of 2026-10. Thinking
# tokens are billed as output. Gemini 3.6/3.8 Flash: Google's regular price from
# 2027 (the introductory price until the end of 2026 is half) - a limit is never
# undercut.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-3.8-flash": (1.50, 7.50),
    "gemini-3.6-flash": (1.50, 7.50),
    "gemini-3-flash-preview": (0.50, 3.0),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
}
# Prompts longer than the threshold are billed at a second rate card.
LONG_PRICES: dict[str, tuple[int, tuple[float, float]]] = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50)),
    "gemini-3.1-pro-preview": (200_000, (4.0, 18.0)),
    "gemini-2.5-pro": (200_000, (2.50, 15.0)),
}
# Cache reads cost a fraction of the input price (0.05x on Opus 5.5, 0.25x on
# Gemini 2.5, 0.1x elsewhere); cache writes 1.25x (Claude, 5-minute entries;
# Gemini caches by itself and charges nothing extra for it).
CACHE_READ = {"claude-opus-5-5": 0.05, "gemini-2.5-pro": 0.25, "gemini-2.5-flash": 0.25}
CACHE_READ_DEFAULT = 0.1
CACHE_WRITE = 1.25


def price(model: str) -> tuple[float, float]:
    # Unknown (a server-side fallback model, a Gemini model loaded from Google, a
    # dated version like "gemini-3.8-flash-001"): the known base name, else the most
    # expensive price of its provider so limits are never undercut.
    if model in PRICES:
        return PRICES[model]
    base = next((m for m in sorted(PRICES, key=len, reverse=True) if model.startswith(m)), None)
    if base is not None:
        return PRICES[base]
    family = "gemini-" if model.startswith("gemini-") else "claude-"
    return max(p for m, p in PRICES.items() if m.startswith(family))


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """What one call costs, from the token counts the API reports (input_tokens: the
    uncached part of the prompt)."""
    p_in, p_out = price(model)
    prompt = input_tokens + cache_read_tokens + cache_write_tokens
    long = LONG_PRICES.get(model)
    if long is not None and prompt > long[0]:
        p_in, p_out = long[1]
    read = CACHE_READ.get(model, CACHE_READ_DEFAULT)
    return (
        input_tokens * p_in
        + cache_read_tokens * p_in * read
        + cache_write_tokens * p_in * CACHE_WRITE
        + output_tokens * p_out
    ) / 1e6


class UsageLedger:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    @staticmethod
    def _month() -> str:
        return datetime.now().strftime("%Y-%m")

    def _read(self) -> dict[str, float]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def month_spent(self) -> float:
        with self._lock:
            return self._read().get(self._month(), 0.0)

    def add(self, usd: float) -> None:
        with self._lock:
            data = self._read()
            month = self._month()
            data[month] = round(data.get(month, 0.0) + usd, 6)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
