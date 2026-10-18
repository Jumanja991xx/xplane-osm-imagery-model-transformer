"""Stand-in for the Claude API: answers from the OSM tags in the request."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from xpsb.ai.analyze import (
    AiAuthError,
    AiBatchResult,
    AiCallError,
    AiItemResult,
    AiOption,
    AiQuestion,
    AiResponse,
)
from xpsb.ai.detect import AiFind, AiFindings

_spec = importlib.util.spec_from_file_location(
    "make_speedway", Path(__file__).parent / "fixtures" / "make_speedway.py"
)
speedway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(speedway)


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

    def __call__(
        self, *, model: str, system: str, content: list[dict], output_format
    ) -> AiResponse:
        self.calls.append(
            {"model": model, "system": system, "content": content, "format": output_format.__name__}
        )
        if self.fail_auth:
            raise AiAuthError("AuthenticationError")
        if self.fail_call:
            raise AiCallError(self.fail_call)
        if output_format is AiFindings:
            return self._search(content[0]["text"], model)
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

    # The object search: "sees" the unmapped grandstand of the fixture image, a bit
    # too large and shifted like a real model would, plus finds that must be dropped.
    def _search(self, text: str, model: str) -> AiResponse:
        m = re.search(r"(\d+)x(\d+) px.*lon ([\d.]+)\.\.([\d.]+), lat ([\d.]+)\.\.([\d.]+)", text)
        w, h = int(m.group(1)), int(m.group(2))
        west, east, south, north = (float(m.group(i)) for i in (3, 4, 5, 6))

        def px(dx: float, dy: float) -> list[float]:
            p = speedway.pt(dx, dy)
            return [(p["lon"] - west) / (east - west) * w, (north - p["lat"]) / (north - south) * h]

        x0, y0, x1, y1 = speedway.UNMAPPED_GRANDSTAND_M
        finds = []
        cx, cy = px((x0 + x1) / 2, (y0 + y1) / 2)
        if 0 <= cx < w and 0 <= cy < h:
            finds += [
                AiFind(
                    kind="object",
                    category="grandstands",
                    polygon=[
                        px(x0 - 5, y0 + 3),
                        px(x1 + 4, y0 + 3),
                        px(x1 + 4, y1 + 4),
                        px(x0 - 5, y1 + 4),
                    ],
                    name="Gegengerade",
                    height_m=7,
                    confidence=0.8,
                    summary="Unkartierte Tribüne an der Gegengeraden.",
                ),
                # The main grandstand is in OSM already: must be dropped.
                AiFind(
                    kind="object",
                    category="grandstands",
                    polygon=[px(-60, 50), px(60, 50), px(60, 68), px(-60, 68)],
                    name=None,
                    height_m=9,
                    confidence=0.9,
                    summary="Bekannt.",
                ),
                AiFind(
                    kind="object",
                    category="masts",
                    polygon=[px(0, 0), px(1, 0), px(1, 1)],
                    name=None,
                    height_m=None,
                    confidence=0.2,
                    summary="Unsicher.",
                ),
                AiFind(
                    kind="object",
                    category="forest",
                    polygon=[px(0, 0), px(9, 0), px(9, 9)],
                    name=None,
                    height_m=None,
                    confidence=0.9,
                    summary="Falsche Kategorie.",
                ),
            ]
        return AiResponse(
            parsed=AiFindings(finds=finds),
            model=self.served_model or model,
            input_tokens=2000,
            output_tokens=500,
        )
