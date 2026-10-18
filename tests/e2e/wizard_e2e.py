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
    for label in (
        "Überdachungen & Zelte",
        "Zäune, Mauern, Leitplanken",
        "Masten (Flutlicht, Fahnen)",
        "Rasen",
        "Asphalt",
        "Sand/Asche (z. B. Speedway)",
    ):
        page.get_by_label(label).check()
    page.screenshot(path=OUT / "03-brief.png", full_page=True)
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
    page.get_by_test_id("accept-ai/1").click()
    expect(page.get_by_test_id("item-ai/1")).to_be_visible(timeout=5000)
    # Open AI questions: take the suggestions.
    page.get_by_test_id("accept-suggestions").click()
    page.wait_for_timeout(500)
    page.screenshot(path=OUT / "07-edited.png")

    page.get_by_test_id("analysis-confirm").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.screenshot(path=OUT / "08-gate-b.png")
    expect(page.get_by_role("dialog")).to_contain_text(
        "7 Objekte und 3 Flächen werden übernommen (1 ausgeschlossen)"
    )
    page.get_by_test_id("gate-approve").click()

    expect(page.get_by_test_id("plan-build")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("plan-confirmed")).to_contain_text("Gerüste")
    page.screenshot(path=OUT / "09-plan.png")
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
    browser.close()

print("CONSOLE ERRORS:" if errors else "no console errors")
for e in errors:
    print(" ", e)
