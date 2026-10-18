"""Per-job event log, streamed to the UI over WebSocket and kept on disk."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal["state", "progress", "log", "error"]


class JobEvent(BaseModel):
    seq: int
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: EventType
    state: str | None = None
    stage: str | None = None
    progress: float | None = None  # 0..1 within the stage
    key: str | None = None  # i18n message key
    params: dict[str, str | int | float | None] = Field(default_factory=dict)
    text: str | None = None  # untranslated technical detail (logs)


class EventLog:
    def __init__(self, file: Path):
        self._file = file
        self._events: list[JobEvent] = []
        self._subscribers: set[asyncio.Queue[JobEvent]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def events(self) -> list[JobEvent]:
        return list(self._events)

    def publish(self, **fields) -> JobEvent:
        event = JobEvent(seq=len(self._events) + 1, **fields)
        self._events.append(event)
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")
        for q in list(self._subscribers):
            q.put_nowait(event)
        return event

    def publish_threadsafe(self, **fields) -> None:
        """For worker threads (DSFTool runs, file copies)."""
        if self._loop is None:
            self.publish(**fields)
        else:
            self._loop.call_soon_threadsafe(lambda: self.publish(**fields))

    def subscribe(self) -> asyncio.Queue[JobEvent]:
        q: asyncio.Queue[JobEvent] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[JobEvent]) -> None:
        self._subscribers.discard(q)


def read_events(file: Path) -> list[JobEvent]:
    if not file.exists():
        return []
    return [
        JobEvent.model_validate(json.loads(line))
        for line in file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
