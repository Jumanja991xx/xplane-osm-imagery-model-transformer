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


def open_settings(page, tab: str) -> None:
    """Settings live in the menu, grouped in tabs."""
    page.get_by_test_id("menu").click()
    page.get_by_test_id("open-settings").click()
    page.get_by_test_id(f"settings-tab-{tab}").click()


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
    expect(page.get_by_role("heading", name="Gebiet wählen")).to_be_visible(timeout=20000)

    # Imagery from a custom source (the fixture backend serves it offline).
    open_settings(page, "imagery")
    page.get_by_test_id("imagery-source").select_option("custom")
    page.get_by_test_id("imagery-url").fill("https://wms.example.test/dop")
    page.get_by_test_id("imagery-attribution").fill("Fixture-Luftbild")
    page.get_by_test_id("settings-save").click()
    page.wait_for_timeout(300)
    # Simulated Claude (XPSB_FAKE_AI): the key only lives in memory.
    page.get_by_test_id("settings-tab-ai").click()
    page.get_by_test_id("api-key").fill("sk-ant-e2e-dummy")
    page.get_by_test_id("api-key-save").click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Schließen", exact=True).click()
    page.wait_for_function("window.__xpsbMap && window.__xpsbMap.loaded()", timeout=20000)
    # Place search (fixture answers): suggestions while typing, the map flies there.
    page.get_by_test_id("place-search-input").fill("hockenheimring")
    expect(page.get_by_test_id("place-results").locator("li")).to_have_count(3, timeout=5000)
    page.get_by_test_id("place-results").locator("li", has_text="Hockenheimring").click()
    page.wait_for_timeout(1500)
    lng = page.evaluate("window.__xpsbMap.getCenter().lng")
    assert abs(lng - 8.566) < 0.01, lng
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
    expect(page.get_by_role("heading", name="Was soll gebaut werden?")).to_be_visible()
    # Accents are not allowed in folder names: the form says so and offers a fix.
    page.get_by_test_id("package-name").fill("Igazságügyi palota")
    expect(page.get_by_test_id("package-name-invalid")).to_be_visible()
    page.get_by_test_id("package-name-fix").click()
    expect(page.get_by_test_id("package-name")).to_have_value("Igazsagugyi palota")
    expect(page.get_by_test_id("package-name-invalid")).to_be_hidden()
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
    open_settings(page, "imagery")
    page.get_by_test_id("imagery-rights").check()
    page.get_by_test_id("settings-save").click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Schließen", exact=True).click()
    page.get_by_test_id("brief-submit").click()
    expect(page.get_by_test_id("gate-approve")).to_be_enabled()
    expect(page.get_by_role("dialog")).to_contain_text("Dauer: meist etwa")
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
    # Undo brings it back as it was; deleting again keeps it deleted.
    expect(page.get_by_test_id("undo-note")).to_be_visible()
    page.get_by_test_id("undo-delete").click()
    expect(page.get_by_test_id("item-node/201")).to_have_count(1, timeout=5000)
    if not page.get_by_test_id("item-node/201").get_by_test_id("delete-item").is_visible():
        page.get_by_test_id("item-node/201").get_by_role("button").first.click()
    page.get_by_test_id("item-node/201").get_by_test_id("delete-item").click()
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

    # Step 3 says which objects the AI architect will rebuild; photos can be added now.
    targets = page.get_by_test_id("design-targets")
    expect(targets).to_be_visible()
    page.screenshot(path=OUT / "07d-design-targets.png")
    targets.get_by_role("button").first.click()
    photos = page.get_by_test_id("item-photos")
    expect(photos).to_be_visible(timeout=5000)
    before = photos.get_by_test_id("photo-list").locator("img").count()
    photos.get_by_text("Ich darf diese Fotos dafür verwenden").click()
    photos.get_by_test_id("photo-upload").set_input_files(
        str(Path(__file__).parents[2] / "backend/tests/fixtures/speedway/photos/sample.jpg")
    )
    expect(photos.get_by_test_id("photo-list").locator("img")).to_have_count(
        before + 1, timeout=10000
    )
    page.screenshot(path=OUT / "07e-step3-photo.png")

    page.get_by_test_id("analysis-confirm").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.screenshot(path=OUT / "08-gate-b.png")
    expect(page.get_by_role("dialog")).to_contain_text(
        "7 Objekte und 3 Flächen werden übernommen (1 ausgeschlossen)"
    )
    page.get_by_test_id("gate-approve").click()

    # The AI architect rebuilds the important objects first (live progress per object).
    expect(page.get_by_test_id("plan-design-progress")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("design-object").first).to_be_visible()
    page.wait_for_timeout(2500)
    page.screenshot(path=OUT / "08b-planning.png")
    expect(page.get_by_test_id("plan-build")).to_be_visible(timeout=120000)
    expect(page.get_by_test_id("plan-confirmed")).to_contain_text("Gerüste")
    expect(page.get_by_test_id("plan-confirmed")).to_contain_text("3 × Tribünen")
    page.screenshot(path=OUT / "09-plan.png")
    # X-Plane folder with a small library: the architect may place its objects.
    lib = Path(XPLANE) / "Resources/default scenery/1000 roads"
    (lib / "objects").mkdir(parents=True, exist_ok=True)
    (lib / "objects/lamp.obj").write_text(
        "I\n800\nOBJ\n\nVT -0.4 0 -0.2 0 1 0 0 0\nVT 0.4 8 0.2 0 1 0 0 0\n"
    )
    (lib / "library.txt").write_text(
        "A\n800\nLIBRARY\n\nEXPORT lib/street/lights/street_lamp.obj objects/lamp.obj\n"
    )
    open_settings(page, "general")
    page.get_by_test_id("xplane-path").fill(XPLANE)
    page.get_by_test_id("settings-save").click()
    page.wait_for_timeout(500)
    page.screenshot(path=OUT / "12-settings.png")
    page.get_by_role("button", name="Schließen", exact=True).click()
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
    page.screenshot(path=OUT / "09c2-designed-object.png")
    # Built automatically by the AI architect: back to the default script (free).
    expect(page.get_by_test_id("design-reset")).to_be_visible()
    page.get_by_test_id("design-reset").click()
    page.get_by_test_id("adjust-flip").click()
    expect(page.get_by_test_id("adjust-flip")).to_be_enabled(timeout=15000)
    page.get_by_test_id("adjust-rows").fill("6")
    page.get_by_test_id("adjust-rows").press("Enter")
    expect(page.get_by_test_id("adjust-rows")).to_be_enabled(timeout=15000)
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "09d-preview-adjusted.png")
    # The same for a building: roof shape, facade, ridge direction.
    page.get_by_test_id("preview-objects").get_by_role("button", name="Fahrerlager-Halle").click()
    if page.get_by_test_id("design-reset").is_visible():
        page.get_by_test_id("design-reset").click()
    expect(page.get_by_test_id("adjust-rows")).to_have_count(0)  # only for stands
    page.get_by_test_id("adjust-roof-shape").select_option("gabled")
    expect(page.get_by_test_id("adjust-wall")).to_be_enabled(timeout=15000)
    page.get_by_test_id("adjust-wall").select_option("brick")
    expect(page.get_by_test_id("adjust-flip")).to_be_enabled(timeout=15000)
    page.get_by_test_id("adjust-flip").click()
    expect(page.get_by_test_id("adjust-flip")).to_be_enabled(timeout=15000)
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "09d2-preview-building.png")
    # Placement on the ground: nudge buttons, then a drag with the move tool.
    page.get_by_title("1 m nach Osten").click()
    expect(page.get_by_test_id("placement-state")).to_contain_text("1.0 m Ost", timeout=15000)
    page.get_by_role("button", name="⟳ 90°").click()
    expect(page.get_by_test_id("placement-state")).to_contain_text("Drehung 90.0°", timeout=15000)
    page.get_by_test_id("place-move").click()
    canvas = page.get_by_test_id("preview-canvas").bounding_box()
    px, py = canvas["x"] + canvas["width"] / 2, canvas["y"] + canvas["height"] * 0.6
    page.mouse.move(px, py)
    page.mouse.down()
    page.mouse.move(px + 120, py, steps=8)
    page.mouse.up()
    expect(page.get_by_test_id("placement-state")).not_to_contain_text("1.0 m Ost", timeout=15000)
    page.get_by_test_id("place-move").click()
    page.wait_for_timeout(800)
    page.screenshot(path=OUT / "09d3-preview-moved.png")
    page.get_by_test_id("place-reset").click()
    expect(page.get_by_test_id("placement-state")).to_contain_text("Drehung 0.0°", timeout=15000)
    # Several objects at once: Ctrl+A, all 20 % lower, then one AI request for two of them.
    page.get_by_test_id("preview-canvas").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+a")
    expect(page.get_by_test_id("multi-controls")).to_be_visible(timeout=5000)
    page.screenshot(path=OUT / "09d4-preview-multi.png")
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("multi-controls")).to_have_count(0)
    page.get_by_test_id("preview-objects").get_by_role("button", name="Fahrerlager-Halle").click()
    page.get_by_test_id("preview-objects").get_by_role("button", name="Clubhaus").click(modifiers=["Control"])
    expect(page.get_by_test_id("multi-controls")).to_contain_text("2 Objekte")
    page.get_by_test_id("multi-height--20").click()
    expect(page.get_by_test_id("multi-height--20")).to_be_enabled(timeout=30000)
    page.get_by_test_id("multi-ai-text").fill("Mach Läden ins Erdgeschoss")
    page.get_by_test_id("multi-ai-estimate").click()
    page.get_by_test_id("multi-ai-send").click()
    expect(page.get_by_test_id("multi-ai-results")).to_contain_text("Gekostet", timeout=60000)
    page.screenshot(path=OUT / "09d5-preview-multi-ai.png")
    page.get_by_text("Auswahl aufheben").click()
    # AI architect (fake AI): own words → cost first → send → rebuilt object.
    page.get_by_test_id("preview-objects").get_by_role("button", name="Clubhaus").click()
    # Photos for the architect: an own one (rights confirmed) and one from open sources.
    page.get_by_text("Ich darf diese Fotos dafür verwenden").click()
    page.get_by_test_id("photo-upload").set_input_files(
        str(Path(__file__).parents[2] / "backend/tests/fixtures/speedway/photos/sample.jpg")
    )
    expect(page.get_by_test_id("photo-list").locator("img")).to_have_count(1, timeout=10000)
    page.get_by_test_id("photo-search").click()
    page.get_by_test_id("photo-search-go").click()
    expect(page.get_by_test_id("photo-candidate").first).to_be_visible(timeout=10000)
    page.wait_for_timeout(800)
    page.screenshot(path=OUT / "09e0-photo-search.png")
    page.get_by_test_id("photo-candidate").first.click()
    expect(page.get_by_test_id("photo-list").locator("img")).to_have_count(2, timeout=10000)
    # A facade cut out of the own photo: corners on the photo, straightened, onto the model.
    page.get_by_test_id("photo-as-texture").first.click()
    editor = page.get_by_test_id("texture-editor")
    expect(editor).to_be_visible()
    editor.get_by_test_id("texture-width").fill("12")
    editor.get_by_test_id("texture-height").fill("6")
    editor.get_by_text("Auf alle Wände").click()
    editor.get_by_test_id("texture-check").click()
    expect(editor.get_by_test_id("texture-result")).to_be_visible(timeout=10000)
    page.screenshot(path=OUT / "09e1-texture-editor.png")
    editor.get_by_test_id("texture-save").click()
    expect(page.get_by_test_id("texture-list")).to_contain_text("front", timeout=15000)
    page.wait_for_timeout(2000)
    page.screenshot(path=OUT / "09e1b-photo-facade.png")
    # Night: lit windows and the objects' lights.
    page.get_by_test_id("preview-night").click()
    page.wait_for_timeout(2000)
    page.screenshot(path=OUT / "09e1c-night.png")
    page.get_by_test_id("preview-night").click()
    # A small model that does not manage: the app offers a stronger one (cost shown again).
    page.get_by_test_id("design-text").fill("zu schwer: Glashalle mit rundem Turm und Laternen an den Ecken")
    page.get_by_test_id("design-model").select_option("claude-haiku-5-5")
    expect(page.get_by_test_id("design-send")).to_contain_text("USD", timeout=10000)
    expect(page.get_by_test_id("design-panel")).to_contain_text("Haiku")
    page.get_by_test_id("design-send").click()
    expect(page.get_by_test_id("design-try-stronger")).to_be_visible(timeout=30000)
    page.screenshot(path=OUT / "09e-design-try-stronger.png")
    page.get_by_test_id("design-try-stronger").get_by_role("button").click()
    expect(page.get_by_test_id("design-model")).to_have_value("claude-opus-5-5")
    expect(page.get_by_test_id("design-panel")).to_contain_text("Opus", timeout=10000)
    expect(page.get_by_test_id("design-send")).to_be_visible(timeout=10000)
    page.screenshot(path=OUT / "09e-design-estimate.png")
    page.get_by_test_id("design-send").click()
    expect(page.get_by_test_id("design-progress")).to_be_visible(timeout=5000)
    page.wait_for_timeout(2500)
    page.screenshot(path=OUT / "09e2-design-progress.png")
    expect(page.get_by_test_id("design-reset")).to_be_visible(timeout=30000)
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "09f-design-done.png")
    # Library objects the architect placed are named (stand-ins in the 3D view).
    expect(page.get_by_test_id("library-list")).to_contain_text("Lampe/Mast")
    # Single parts: the tower 25 % higher and turned, then undone once; the AI only for it.
    page.get_by_test_id("parts-toggle").click()
    page.get_by_test_id("part-Turm").click()
    page.wait_for_function(
        "window.__xpsbPreview.scene.getObjectByName('part-highlight') !== undefined", timeout=5000
    )
    expect(page.get_by_test_id("parts-controls")).to_contain_text("1 Teil")
    page.get_by_test_id("part-height-25").click()
    expect(page.get_by_test_id("parts-panel")).to_contain_text("1 Änderung", timeout=20000)
    page.get_by_test_id("part-turn-15").click()
    expect(page.get_by_test_id("parts-panel")).to_contain_text("2 Änderung", timeout=20000)
    page.wait_for_function(  # still marked on the rebuilt object
        "window.__xpsbPreview.scene.getObjectByName('part-highlight') !== undefined", timeout=5000
    )
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "09f2-parts.png")
    page.get_by_test_id("parts-undo").click()
    expect(page.get_by_test_id("parts-panel")).to_contain_text("1 Änderung", timeout=20000)
    expect(page.get_by_test_id("design-parts")).to_contain_text("Nur für: Turm")
    page.get_by_test_id("part-Halle").click(modifiers=["Control"])
    expect(page.get_by_test_id("parts-controls")).to_contain_text("2 Teil")
    page.get_by_test_id("parts-toggle").click()
    page.get_by_test_id("preview-close").click()
    # One step back to the list and forward again: nothing is lost, no new analysis.
    page.get_by_test_id("back-to-list").click()
    expect(page.get_by_test_id("item-ai/1")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("item-way/103")).to_contain_text("12 m")
    page.get_by_test_id("analysis-confirm").click()
    page.get_by_test_id("gate-approve").click()
    expect(page.get_by_test_id("plan-build")).to_be_visible(timeout=20000)
    # Cancel needs a confirmation.
    page.get_by_test_id("menu").click()
    page.get_by_test_id("cancel-job").click()
    expect(page.get_by_test_id("cancel-confirm")).to_be_visible()
    page.get_by_role("button", name="Nein", exact=True).click()
    page.get_by_test_id("plan-build").click()
    page.screenshot(path=OUT / "10-gate-c.png")
    page.get_by_test_id("gate-approve").click()

    expect(page.get_by_test_id("install-button")).to_be_visible(timeout=60000)
    # Last chance to rename the scenery folder (accents are not allowed in the name).
    page.get_by_test_id("package-name").fill("E2E Umbenannt")
    page.get_by_test_id("package-rename").click()
    expect(page.get_by_text("E2E Umbenannt", exact=False).last).to_be_visible(timeout=10000)
    page.screenshot(path=OUT / "12b-rename.png")
    page.get_by_test_id("install-button").click()
    expect(page.get_by_test_id("gate-approve")).to_be_enabled(timeout=10000)
    page.screenshot(path=OUT / "13-gate-d.png")
    page.get_by_test_id("gate-approve").click()
    expect(page.get_by_test_id("install-done")).to_be_visible(timeout=30000)
    assert (Path(XPLANE) / "Custom Scenery" / "E2E Umbenannt").is_dir()
    page.screenshot(path=OUT / "14-installed.png")
    # The finished result can still be looked at in 3D.
    page.get_by_test_id("result-preview").click()
    page.wait_for_timeout(4000)
    page.screenshot(path=OUT / "15-result-preview.png")
    page.keyboard.press("Escape")
    if page.get_by_test_id("preview-close").count():
        page.get_by_test_id("preview-close").click()
    # One step back from the installed result: the plan and its preview stay.
    page.get_by_test_id("back-to-plan").click()
    expect(page.get_by_test_id("plan-build")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("plan-confirmed")).to_contain_text("3 × Tribünen")
    page.screenshot(path=OUT / "16-back-to-plan.png")
    # All jobs in the menu.
    page.get_by_test_id("menu").click()
    page.screenshot(path=OUT / "17-menu.png")
    page.get_by_test_id("menu-jobs").click()
    expect(page.get_by_test_id("jobs-list")).to_contain_text("E2E Umbenannt")
    page.screenshot(path=OUT / "18-jobs.png")
    page.get_by_role("button", name="Schließen", exact=True).click()
    open_settings(page, "ai")
    # A Gemini key next to the Claude key (simulated store: memory only).
    page.get_by_test_id("gemini-key").fill("AIza-e2e-dummy")
    page.get_by_test_id("gemini-key-save").click()
    expect(page.get_by_test_id("gemini-key-status")).to_contain_text("hinterlegt")
    expect(page.get_by_test_id("settings-model").locator("optgroup")).to_have_count(2)
    expect(page.get_by_test_id("job-limit")).to_be_visible()
    page.get_by_test_id("job-limit").fill("60")
    expect(page.get_by_role("dialog")).to_contain_text("Das Monatslimit (20 USD) ist kleiner")
    page.get_by_test_id("monthly-limit").scroll_into_view_if_needed()
    page.screenshot(path=OUT / "19-settings-ai.png")
    page.get_by_test_id("job-limit").fill("5")
    page.get_by_role("button", name="Schließen", exact=True).click()
    browser.close()

print("CONSOLE ERRORS:" if errors else "no console errors")
for e in errors:
    print(" ", e)
