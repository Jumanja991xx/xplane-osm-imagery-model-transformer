"""Browser test of the full wizard against the dev backend (M2, offline fixtures).

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
        args=[
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
        ],
    )
    page = browser.new_page(viewport={"width": 1400, "height": 900}, locale="de-DE")
    page.on(
        "console",
        lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto("http://localhost:1420/")
    expect(page.get_by_role("heading", name="Bereich")).to_be_visible(timeout=20000)

    # Imagery from a custom source (the fixture backend serves it offline).
    page.get_by_test_id("open-settings").click()
    page.get_by_test_id("imagery-source").select_option("custom")
    page.get_by_test_id("imagery-url").fill("https://wms.example.test/dop")
    page.get_by_test_id("imagery-attribution").fill("Fixture-Luftbild")
    page.get_by_test_id("settings-save").click()
    page.wait_for_timeout(300)
    # Simulated Claude (XPSB_FAKE_AI): the key only lives in memory.
    page.get_by_test_id("api-key").fill("sk-ant-e2e-dummy")
    page.get_by_test_id("api-key-save").click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Schließen", exact=True).click()
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
    expect(page.get_by_test_id("find-everything")).to_be_checked()
    page.screenshot(path=OUT / "03-brief.png", full_page=True)
    page.get_by_test_id("surface-mode-imagery").check()
    page.screenshot(path=OUT / "03b-brief-surfaces.png", full_page=True)
    page.get_by_test_id("brief-submit").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.screenshot(path=OUT / "04-gate-a-blocked.png")
    # A custom source needs the rights confirmation first.
    expect(page.get_by_test_id("gate-approve")).to_be_disabled()
    page.get_by_role("button", name="Abbrechen", exact=True).click()
    page.get_by_test_id("open-settings").click()
    page.get_by_test_id("imagery-rights").check()
    page.get_by_test_id("settings-save").click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Schließen", exact=True).click()
    page.get_by_test_id("open-gate-a").click()
    expect(page.get_by_test_id("gate-approve")).to_be_enabled()
    page.screenshot(path=OUT / "04-gate-a.png")
    page.get_by_test_id("gate-approve").click()

    # Analysis: 10 items from OSM, aerial image under them.
    expect(page.get_by_test_id("item-way/103")).to_be_visible(timeout=30000)
    page.wait_for_timeout(1500)  # imagery + fitBounds
    page.screenshot(path=OUT / "05-analysis.png")
    assert page.locator("[data-testid^='item-']").count() == 10
    # The AI object search found the grandstand that OSM does not know.
    expect(page.get_by_test_id("proposal-ai/1")).to_be_visible()
    expect(page.get_by_test_id("ai-summary")).to_contain_text("Vorschläge")

    # List -> map: select the grandstand, change its height.
    page.get_by_test_id("item-way/103").get_by_role("button").first.click()
    page.get_by_test_id("edit-height").fill("12")
    page.get_by_test_id("edit-category").focus()  # blur saves
    expect(page.get_by_test_id("item-way/103")).to_contain_text("12 m", timeout=5000)
    # Map -> list: click the mast on the map.
    mast = page.evaluate("""() => {
        const m = window.__xpsbMap;
        const f = m.querySourceFeatures('scene').find(f => f.properties.itemId === 'node/201');
        const p = m.project(f.geometry.coordinates);
        return [p.x, p.y];
    }""")
    mb = page.get_by_test_id("map").bounding_box()
    page.mouse.click(mb["x"] + mast[0], mb["y"] + mast[1])
    expect(page.get_by_test_id("item-node/201").get_by_test_id("delete-item")).to_be_visible()
    page.screenshot(path=OUT / "06-selected-mast.png")
    page.get_by_test_id("delete-item").click()
    expect(page.get_by_test_id("item-node/201")).to_have_count(0)
    # Exclude the fence.
    page.get_by_test_id("include-way/107").uncheck()
    # Draw a new scaffolding next to the grandstand.
    page.get_by_test_id("new-item-category").select_option("object:scaffolding")
    page.get_by_test_id("new-item-draw").click()
    x0, y0 = mb["x"] + mb["width"] / 2 - 30, mb["y"] + 60
    for dx, dy in ((0, 0), (40, 0), (40, 25), (0, 25), (0, 0)):
        page.mouse.click(x0 + dx, y0 + dy)
        page.wait_for_timeout(150)
    expect(page.get_by_test_id("item-user/1")).to_be_visible(timeout=5000)
    page.get_by_test_id("proposal-ai/1").get_by_role("button").first.click()
    page.wait_for_timeout(600)
    page.screenshot(path=OUT / "07a-proposal.png")
    # Bulk change: mark, choose a category, accept.
    page.get_by_test_id("mark-ai/1").check()
    page.get_by_test_id("bulk-category").select_option("grandstands")
    page.get_by_test_id("bulk-accept").click()
    expect(page.get_by_test_id("item-ai/1")).to_be_visible(timeout=5000)
    # "Find similar" asks with the cost first, then searches.
    page.get_by_test_id("item-way/103").get_by_role("button").first.click()
    page.get_by_test_id("find-similar").click()
    expect(page.get_by_test_id("similar-confirm")).to_contain_text("USD")
    page.screenshot(path=OUT / "07b-similar-confirm.png")
    page.get_by_test_id("similar-go").click()
    expect(page.get_by_text("0 neue Vorschläge.")).to_be_visible(timeout=20000)
    # Open AI questions: take the suggestions.
    page.get_by_test_id("accept-suggestions").click()
    page.wait_for_timeout(500)
    page.screenshot(path=OUT / "07-edited.png")
    # Several at once: the pit roof becomes a grandstand.
    page.get_by_test_id("multi-edit").click()
    page.get_by_test_id("mark-way/106").check()
    page.get_by_test_id("multi-category").select_option("grandstands")
    page.get_by_test_id("multi-apply").click()
    page.wait_for_timeout(500)
    page.get_by_test_id("multi-edit").click()
    # Selecting items far down the list must not scroll the app frame away.
    page.get_by_test_id("item-way/107").get_by_role("button").first.click()
    page.wait_for_timeout(600)
    expect(page.get_by_role("heading", name="X-Plane AI-Scenery Builder")).to_be_in_viewport()
    expect(page.get_by_test_id("toggle-3d")).to_be_in_viewport()
    page.get_by_test_id("toggle-3d").click()
    page.wait_for_timeout(1200)
    page.screenshot(path=OUT / "07c-3d.png")
    page.get_by_test_id("toggle-3d").click()

    page.get_by_test_id("analysis-confirm").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.screenshot(path=OUT / "08-gate-b.png")
    expect(page.get_by_role("dialog")).to_contain_text(
        "7 Objekte und 3 Flächen werden übernommen (1 ausgeschlossen)"
    )
    page.get_by_test_id("gate-approve").click()

    expect(page.get_by_test_id("plan-build")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("plan-confirmed")).to_contain_text("Gerüste")
    expect(page.get_by_test_id("plan-confirmed")).to_contain_text("3 × Tribünen")
    page.screenshot(path=OUT / "09-plan.png")
    page.get_by_test_id("open-preview").click()
    expect(page.get_by_test_id("preview-objects")).to_be_visible(timeout=30000)
    page.wait_for_timeout(2500)
    page.screenshot(path=OUT / "09b-preview.png")
    page.get_by_test_id("preview-objects").get_by_role("button").first.click()
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "09c-preview-closeup.png")
    # Quick controls: turn the main stand around and give it 6 rows; only it is rebuilt.
    page.get_by_test_id("preview-objects").get_by_role("button", name="Haupttribüne").click()
    expect(page.get_by_test_id("object-controls")).to_be_visible()
    page.get_by_test_id("adjust-flip").click()
    expect(page.get_by_test_id("adjust-flip")).to_be_enabled(timeout=15000)
    page.get_by_test_id("adjust-rows").fill("6")
    page.get_by_test_id("adjust-rows").press("Enter")
    expect(page.get_by_test_id("adjust-rows")).to_be_enabled(timeout=15000)
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "09d-preview-adjusted.png")
    page.get_by_test_id("preview-close").click()
    # One step back to the list and forward again: nothing is lost, no new analysis.
    page.get_by_test_id("back-to-list").click()
    expect(page.get_by_test_id("item-ai/1")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("item-way/103")).to_contain_text("12 m")
    page.get_by_test_id("analysis-confirm").click()
    page.get_by_test_id("gate-approve").click()
    expect(page.get_by_test_id("plan-build")).to_be_visible(timeout=20000)
    # Cancel needs a confirmation.
    page.get_by_test_id("cancel-job").click()
    expect(page.get_by_test_id("cancel-confirm")).to_be_visible()
    page.get_by_role("button", name="Nein", exact=True).click()
    page.get_by_test_id("plan-build").click()
    page.screenshot(path=OUT / "10-gate-c.png")
    page.get_by_test_id("gate-approve").click()

    expect(page.get_by_test_id("install-button")).to_be_visible(timeout=60000)
    page.get_by_test_id("install-button").click()
    page.screenshot(path=OUT / "13-gate-d-blocked.png")
    expect(page.get_by_test_id("gate-approve")).to_be_disabled()
    page.get_by_role("button", name="Abbrechen", exact=True).click()

    page.get_by_test_id("open-settings").click()
    page.get_by_test_id("xplane-path").fill(XPLANE)
    page.get_by_test_id("settings-save").click()
    page.wait_for_timeout(500)
    page.screenshot(path=OUT / "12-settings.png")
    page.get_by_role("button", name="Schließen", exact=True).click()

    page.get_by_test_id("install-button").click()
    expect(page.get_by_test_id("gate-approve")).to_be_enabled(timeout=10000)
    page.screenshot(path=OUT / "13-gate-d.png")
    page.get_by_test_id("gate-approve").click()
    expect(page.get_by_test_id("install-done")).to_be_visible(timeout=30000)
    page.screenshot(path=OUT / "14-installed.png")
    # The finished result can still be looked at in 3D.
    page.get_by_test_id("result-preview").click()
    page.wait_for_timeout(4000)
    page.screenshot(path=OUT / "15-result-preview.png")
    browser.close()

print("CONSOLE ERRORS:" if errors else "no console errors")
for e in errors:
    print(" ", e)
