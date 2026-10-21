"""Prices and a small ledger of what the AI cost, so monthly limits hold across jobs."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

# Anthropic's list prices (USD per million tokens: input, output), as of 2026-10.
# Thinking tokens are billed as output.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Prompts longer than this are billed at a second rate card (Haiku 5.5 only).
LONG_PROMPT_TOKENS = 100_000
LONG_PRICES: dict[str, tuple[float, float]] = {"claude-haiku-5-5": (0.50, 2.50)}
# Cache reads cost a fraction of the input price (0.05x on Opus 5.5, 0.1x elsewhere);
# cache writes 1.25x (5-minute entries).
CACHE_READ = {"claude-opus-5-5": 0.05}
CACHE_READ_DEFAULT = 0.1
CACHE_WRITE = 1.25


def price(model: str) -> tuple[float, float]:
    # Unknown (e.g. a server-side fallback model): count with the most expensive
    # price so limits are never undercut.
    return PRICES.get(model, max(PRICES.values()))


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
    if prompt > LONG_PROMPT_TOKENS and model in LONG_PRICES:
        p_in, p_out = LONG_PRICES[model]
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
