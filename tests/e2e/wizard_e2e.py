"""Browser test of the full wizard against the dev backend (M1).

Start backend and frontend first (see tests/e2e/README.md), then:
    uv run --no-project --with playwright python tests/e2e/wizard_e2e.py <fake X-Plane dir>
Set PW_CHROMIUM to a Chromium executable if Playwright's own browser is not installed.
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).parent / "shots"  # gitignored
OUT.mkdir(exist_ok=True)
XPLANE = sys.argv[1]
errors: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get("PW_CHROMIUM") or None,
        args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    )
    page = browser.new_page(viewport={"width": 1400, "height": 900}, locale="de-DE")
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto("http://localhost:1420/")
    expect(page.get_by_role("heading", name="Bereich")).to_be_visible(timeout=20000)
    page.wait_for_function("window.__xpsbMap && window.__xpsbMap.loaded()", timeout=20000)
    page.evaluate("window.__xpsbMap.jumpTo({center: [8.5658, 49.3278], zoom: 16})")
    page.wait_for_timeout(500)
    page.screenshot(path=OUT / "01-start.png")

    page.get_by_test_id("draw-rectangle").click()
    box = page.get_by_test_id("map").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.click(cx - 200, cy - 150)
    page.wait_for_timeout(200)
    page.mouse.move(cx + 200, cy + 150, steps=5)
    page.mouse.click(cx + 200, cy + 150)
    expect(page.get_by_test_id("area-size")).to_be_visible()
    print("area:", page.get_by_test_id("area-size").inner_text())
    page.screenshot(path=OUT / "02-area.png")

    page.get_by_test_id("area-next").click()
    expect(page.get_by_role("heading", name="Auftrag klären")).to_be_visible()
    page.get_by_test_id("package-name").fill("XPSB Test Hockenheim")
    page.screenshot(path=OUT / "03-brief.png", full_page=True)
    page.get_by_test_id("brief-submit").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.screenshot(path=OUT / "04-gate-a.png")
    page.get_by_test_id("gate-approve").click()

    expect(page.get_by_test_id("answer-test_cube_height-20")).to_be_visible(timeout=20000)
    page.get_by_test_id("answer-test_cube_height-20").check()
    page.wait_for_timeout(300)
    page.screenshot(path=OUT / "05-analysis.png")
    page.get_by_test_id("analysis-confirm").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.screenshot(path=OUT / "06-gate-b.png")
    page.get_by_test_id("gate-approve").click()

    expect(page.get_by_test_id("plan-build")).to_be_visible(timeout=10000)
    page.screenshot(path=OUT / "07-plan.png")
    page.get_by_test_id("plan-build").click()
    page.screenshot(path=OUT / "08-gate-c.png")
    page.get_by_test_id("gate-approve").click()

    expect(page.get_by_test_id("install-button")).to_be_visible(timeout=60000)
    page.get_by_test_id("install-button").click()
    page.screenshot(path=OUT / "09-gate-d-blocked.png")
    expect(page.get_by_test_id("gate-approve")).to_be_disabled()
    page.get_by_role("button", name="Abbrechen", exact=True).click()

    page.get_by_test_id("open-settings").click()
    page.get_by_test_id("xplane-path").fill(XPLANE)
    page.get_by_test_id("settings-save").click()
    page.wait_for_timeout(500)
    page.screenshot(path=OUT / "10-settings.png")
    page.get_by_role("button", name="Schließen", exact=True).click()

    page.get_by_test_id("install-button").click()
    expect(page.get_by_test_id("gate-approve")).to_be_enabled(timeout=10000)
    page.screenshot(path=OUT / "11-gate-d.png")
    page.get_by_test_id("gate-approve").click()
    expect(page.get_by_test_id("install-done")).to_be_visible(timeout=30000)
    page.screenshot(path=OUT / "12-installed.png")
    browser.close()

print("CONSOLE ERRORS:" if errors else "no console errors")
for e in errors:
    print(" ", e)
