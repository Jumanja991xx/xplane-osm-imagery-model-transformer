"""Stand-in for the Claude API: answers from the OSM tags in the request."""

from __future__ import annotations

import importlib.util
import os
import re
import time
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
from xpsb.ai.architect import ArchitectReview, ArchitectScript
from xpsb.ai.detect import AiFind, AiFindings

# What the fake architect "writes": the outline as a glass hall with a round tower.
ARCHITECT_SCRIPT = """
h = params.get("height") or 8
extrude(footprint, 0, h, mat="glass", top="roof_flat")
c = polygon_centroid(footprint)
cylinder(c[0], c[1], h, 2.5, 6, mat="brick", segments=12)
"""

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
        self.cut_off = 0  # next n architect answers end at the output limit
        self.refuse = False  # the architect's answers are declined
        self.transport_models: list[str] = []  # the model each transport was made for
        # Browser tests: the architect takes a moment, like the real one (seconds).
        self.architect_delay = float(os.environ.get("XPSB_FAKE_AI_DELAY", "0"))

    def factory(self, api_key: str, model: str = "") -> FakeAi:
        self.keys.append(api_key)
        self.transport_models.append(model)
        return self

    def __call__(
        self,
        *,
        model: str,
        system: str,
        content: list[dict],
        output_format,
        effort="low",
        max_tokens=16000,
    ) -> AiResponse:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "content": content,
                "format": output_format.__name__,
                "effort": effort,
                "max_tokens": max_tokens,
            }
        )
        if self.fail_auth:
            raise AiAuthError("AuthenticationError")
        if self.fail_call:
            raise AiCallError(self.fail_call)
        if output_format in (ArchitectScript, ArchitectReview):
            time.sleep(self.architect_delay)
            if self.refuse:
                return AiResponse(
                    None, model, self.tokens[0], 0, refused=True, stop_reason="refusal"
                )
            if self.cut_off and output_format is ArchitectScript:
                self.cut_off -= 1
                return AiResponse(None, model, self.tokens[0], max_tokens, stop_reason="max_tokens")
        if output_format is ArchitectScript:
            text = "\n".join(b.get("text", "") for b in content)
            broken = "kaputt" in text and "failed in the sandbox" not in text
            script = ARCHITECT_SCRIPT
            lib = re.search(r"^  (lib/\S+\.obj)", text, re.MULTILINE)
            if lib:  # a lamp at each corner of the outline, from the X-Plane library
                script += f"for p in footprint:\n    place('{lib.group(1)}', p[0], p[1], 45)\n"
            return AiResponse(
                parsed=ArchitectScript(
                    script="extrude(footprint, 0, -1)" if broken else script,
                    summary="Glashalle mit rundem Turm.",
                ),
                model=self.served_model or model,
                input_tokens=self.tokens[0],
                output_tokens=self.tokens[1],
            )
        if output_format is ArchitectReview:
            return AiResponse(
                parsed=ArchitectReview(ok=True, problems="", script=None, summary="Passt so."),
                model=self.served_model or model,
                input_tokens=self.tokens[0],
                output_tokens=self.tokens[1],
            )
        if output_format is AiFindings:
            tile_text = next(
                b["text"] for b in content if b["type"] == "text" and " px," in b["text"]
            )
            return self._search(tile_text, model)
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
                    covered=True,
                    roof_material="solar",
                    roof_color=None,
                    seat_color="#C03030",
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
                    covered=None,
                    roof_material=None,
                    roof_color=None,
                    seat_color=None,
                    confidence=0.9,
                    summary="Bekannt.",
                ),
                AiFind(
                    kind="object",
                    category="masts",
                    polygon=[px(0, 0), px(1, 0), px(1, 1)],
                    name=None,
                    height_m=None,
                    covered=None,
                    roof_material=None,
                    roof_color=None,
                    seat_color=None,
                    confidence=0.2,
                    summary="Unsicher.",
                ),
                AiFind(
                    kind="object",
                    category="forest",
                    polygon=[px(0, 0), px(9, 0), px(9, 9)],
                    name=None,
                    height_m=None,
                    covered=None,
                    roof_material=None,
                    roof_color=None,
                    seat_color=None,
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
