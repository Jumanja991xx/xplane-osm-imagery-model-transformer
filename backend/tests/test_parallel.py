"""Objects are built on several CPU cores: same files as one after another, the
progress counts every object, and a broken worker does not lose any."""

from __future__ import annotations

import time

from xpsb.build import assemble, parallel
from xpsb.scene import ItemAttrs, SceneItem


def _items(n: int) -> list[SceneItem]:
    out = []
    for i in range(n):
        x = 11.0 + i * 0.0004
        ring = [[x, 48.0], [x + 0.0002, 48.0], [x + 0.0002, 48.0002], [x, 48.0002], [x, 48.0]]
        out.append(
            SceneItem(
                id=f"way/{i + 1}",
                kind="object",
                category="buildings",
                geometry={"type": "Polygon", "coordinates": [ring]},
                source="osm",
                name=f"Haus {i + 1}",
                attrs=ItemAttrs(height_m=8 + i, roof_shape="gabled" if i % 2 else "flat"),
            )
        )
    return out


def _build(tmp_path, monkeypatch, workers: str, items):
    monkeypatch.setenv("XPSB_BUILD_WORKERS", workers)
    seen = []
    out = tmp_path / f"w{workers}"
    t = time.monotonic()
    res = assemble.generate_all(items, "medium", out, True, lambda *a: seen.append(a[1]))
    return res, out, seen, time.monotonic() - t


def test_parallel_build_gives_the_same_objects(tmp_path, monkeypatch):
    items = _items(6)
    one, out1, seen1, _ = _build(tmp_path, monkeypatch, "1", items)
    many, out3, seen3, _ = _build(tmp_path, monkeypatch, "3", items)
    assert [r.stem for r in many] == [r.stem for r in one]
    assert [r.item_id for r in many] == [r.item_id for r in one]
    assert all(r.status == "ok" for r in many)
    assert [r.triangles for r in many] == [r.triangles for r in one]
    for r in one:
        assert (out3 / f"{r.stem}.json").read_bytes() == (out1 / f"{r.stem}.json").read_bytes()
    assert sorted(seen3) == sorted(seen1) == list(range(1, 7))


def test_workers_by_cores_and_size(monkeypatch):
    monkeypatch.delenv("XPSB_BUILD_WORKERS", raising=False)
    monkeypatch.setattr(parallel.os, "cpu_count", lambda: 8)
    assert parallel.workers(2) == 1  # starting processes would cost more
    assert parallel.workers(5) == 5
    assert parallel.workers(50) == 7  # one core stays free
    monkeypatch.setenv("XPSB_BUILD_WORKERS", "1")
    assert parallel.workers(50) == 1


def test_a_failing_pool_falls_back_to_one_process(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no processes allowed")

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", broken)
    res, out, seen, _ = _build(tmp_path, monkeypatch, "4", _items(5))
    assert [r.status for r in res] == ["ok"] * 5 and sorted(seen) == [1, 2, 3, 4, 5]
