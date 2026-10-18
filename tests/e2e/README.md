# Browser-Test (End-to-End)

Prüft den kompletten Assistenten im echten Browser: Gebiet zeichnen → Freigaben A–D →
Analyse (OSM + Luftbild aus Testdateien) → Editor (ändern, ausschließen, löschen, zeichnen) →
Bau mit DSFTool → Installation in einen Test-X-Plane-Ordner.

```bash
python tools/build_dsftool.py                       # einmalig
mkdir -p "/tmp/xp/X-Plane 12/Custom Scenery" "/tmp/xp/X-Plane 12/Resources"
XPSB_FAKE_AI=$PWD/backend/tests/fake_ai.py XPSB_FIXTURES_DIR=$PWD/backend/tests/fixtures/speedway pnpm dev:backend  # Terminal 1
VITE_BASEMAP_STYLE=blank pnpm dev:frontend          # Terminal 2 (ohne Internet-Karte)
uv run --no-project --with playwright python tests/e2e/wizard_e2e.py "/tmp/xp/X-Plane 12"
```

Screenshots landen in `tests/e2e/shots/`.

`XPSB_FIXTURES_DIR` liefert OSM-Daten und Luftbild aus Testdateien, `XPSB_FAKE_AI` eine simulierte KI
(kein API-Key, keine Kosten; der Test-Key wird nur im Speicher gehalten).
