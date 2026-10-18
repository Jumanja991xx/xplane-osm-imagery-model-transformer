"""Stand-in for the Claude API: answers from the OSM tags in the request."""

from __future__ import annotations

import re

from xpsb.ai.analyze import (
    AiAuthError,
    AiBatchResult,
    AiCallError,
    AiItemResult,
    AiOption,
    AiQuestion,
    AiResponse,
)


class FakeAi:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.keys: list[str] = []
        self.fail_auth = False
        self.fail_call: str | None = None
        self.served_model: str | None = None  # simulate a server-side fallback
        self.tokens = (3000, 1500)

    def factory(self, api_key: str) -> FakeAi:
        self.keys.append(api_key)
        return self

    def __call__(self, *, model: str, system: str, content: list[dict]) -> AiResponse:
        self.calls.append({"model": model, "system": system, "content": content})
        if self.fail_auth:
            raise AiAuthError("AuthenticationError")
        if self.fail_call:
            raise AiCallError(self.fail_call)
        results = []
        for block in content:
            if block["type"] != "text":
                continue
            item_id = re.search(r"item_id: (\S+)", block["text"]).group(1)
            category = re.search(r"current category: (\S+)", block["text"]).group(1)
            unsure = "building=yes" in block["text"] and "height=" not in block["text"]
            results.append(
                AiItemResult(
                    item_id=item_id,
                    category=category,
                    height_m=7.5,
                    levels=None,
                    roof_shape="flat",
                    roof_color="#AA3322",
                    wall_color="not a colour",
                    seat_color=None,
                    material=None,
                    covered=None,
                    confidence=0.5 if unsure else 0.9,
                    summary=f"Fake-Einschätzung für {item_id}",
                    question=AiQuestion(
                        field="height_m",
                        text="Wie hoch ist das Gebäude?",
                        options=[
                            AiOption(value="6", label="6 m"),
                            AiOption(value="9", label="9 m"),
                            AiOption(value="999", label="zu hoch"),
                        ],
                        suggested="9",
                    )
                    if unsure
                    else None,
                )
            )
        return AiResponse(
            parsed=AiBatchResult(items=results),
            model=self.served_model or model,
            input_tokens=self.tokens[0],
            output_tokens=self.tokens[1],
        )
