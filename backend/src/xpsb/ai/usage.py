"""Prices and a small ledger of what the AI cost, so monthly limits hold across jobs."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

# USD per million tokens (input, output). Thinking tokens are billed as output.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def price(model: str) -> tuple[float, float]:
    # Unknown (e.g. a server-side fallback model): count with the most expensive
    # price so limits are never undercut.
    return PRICES.get(model, max(PRICES.values()))


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = price(model)
    return (input_tokens * p_in + output_tokens * p_out) / 1e6


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
