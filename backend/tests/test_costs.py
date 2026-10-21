"""Cheaper AI runs: the right model per step, no pointless calls, cached prefixes."""

from __future__ import annotations

from xpsb.ai import analyze, detect
from xpsb.jobs.models import Brief, scan_model
from xpsb.scene import ItemAttrs, SceneItem

GEO = {"type": "Polygon", "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.001], [0, 0]]]}


def _item(category="buildings", **attrs) -> SceneItem:
    return SceneItem(
        id="way/1",
        kind="object",
        category=category,
        geometry=GEO,
        source="osm",
        attrs=ItemAttrs(**attrs),
    )


def test_looking_at_the_image_runs_on_sonnet_when_opus_is_chosen():
    b = Brief(package_name="X", ai_model="claude-opus-5-5", object_categories=["buildings"])
    assert scan_model(b) == "claude-sonnet-5-5"
    assert scan_model(b.model_copy(update={"ai_economy": False})) == "claude-opus-5-5"
    assert scan_model(b.model_copy(update={"ai_model": "claude-haiku-5-5"})) == "claude-haiku-5-5"


def test_only_items_the_ai_can_add_to_are_rated():
    assert analyze.needs_rating(_item())
    full = _item(height_m=9, roof_shape="gabled", roof_color="#aa3322", wall_color="#eeeeee")
    assert not analyze.needs_rating(full)
    assert not analyze.needs_rating(_item("barriers"))
    left_out = _item()
    left_out.included = False
    assert not analyze.needs_rating(left_out)
    named = full.model_copy(update={"name": "Rathaus"})
    assert analyze.needs_rating(named)  # landmarks are worth a look


def test_second_look_only_where_the_first_found_something():
    tiles = [
        detect.Tile(i, (i % 4) * 100, (i // 4) * 100, (i % 4) * 100 + 100, (i // 4) * 100 + 100)
        for i in range(16)
    ]
    near = detect.around(tiles, {5})
    assert sorted(t.index for t in near) == [0, 1, 2, 4, 5, 6, 8, 9, 10]
    assert detect.around(tiles, set()) == []
