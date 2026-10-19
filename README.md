# X-Plane AI-Scenery Builder

Desktop-App (Windows), die aus einem auf der Karte gezeichneten Gebiet ein
X-Plane-12-Szeneriepaket mit **Objekten und den dazugehörigen Flächen** erzeugt –
Tribünen, Gebäude, Gerüste, Rasen, Asphalt usw. Kostenlos für die Community.

**Grundprinzip:** Die App fragt vor jedem Schritt mit Wirkung (Download, KI-Kosten,
Dateien schreiben, X-Plane-Ordner ändern) und zeigt dabei, *was passiert, was es kostet
und wohin geschrieben wird*.

> **Status: Meilenstein 3.** Aus der bestätigten Liste werden echte 3D-Objekte gebaut –
> über Bauskripte in einer abgeschotteten Skriptsprache (Starlark), auf Wunsch individuell
> von der KI-Architektin (aus Luftbild, Fotos, Wissen). Dazu gehören:
> - **Detail-Baukasten:** Fassaden mit echter Tiefe (Fensterlaibungen, Gesimse, Pilaster),
>   Mansarddächer, Balustraden, Kamine, Normal-Maps.
> - **Fassadenfotos:** Ecken auf einem Foto markieren → die App zieht es gerade und malt es
>   als Textur aufs Modell (nur Fotos, deren Lizenz das erlaubt; Urheber stehen in der README
>   des Pakets). Für Stadien/Bürofassaden reicht ein Muster-Stück.
> - **Nacht:** beleuchtete Fenster (`TEXTURE_LIT`), angestrahlte Wahrzeichen, Flutlicht,
>   Laternen (X-Plane-12-Lichter aus `lights.txt`).
> - **Detailstufen:** nah echte Fenster, weiter weg gemalte Fassaden (zwei `ATTR_LOD`).
> - **Stadion-Baukasten:** zwei Ränge mit Sitzreihen, Werbebande, Lamellenfassade,
>   durchscheinendes Dachinnenteil, Dachträger, Flutlicht.
> - **Wehende Fahnen** (Animation, drehen sich mit dem Wind).
> - **Blender (optional, vom Nutzer installiert):** backt beim Paketbau weiche Schatten
>   (Ambient Occlusion) in die Texturen – mit einem festen Skript, nie mit KI-Code.

Architektur und Roadmap: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ·
geprüfte X-Plane-Formatdetails: [`docs/xplane-format-notes.md`](docs/xplane-format-notes.md)

## Aufbau

```
apps/frontend/          React + TypeScript + Tailwind + MapLibre + Terra Draw (DE/EN)
apps/desktop/src-tauri/ Tauri-Hülle (Rust): startet das Backend unsichtbar, NSIS-Installer
backend/                Python-Backend (FastAPI): Freigaben, X-Plane-Writer, DSFTool
tools/                  DSFTool aus Laminars Quellcode bauen, Paketierung, Smoke-Test
tests/e2e/              Browser-Test des kompletten Assistenten
```

## Entwicklung

Voraussetzungen: Python ≥ 3.12 mit [uv](https://docs.astral.sh/uv/), Node 22 mit pnpm,
Rust (stable), CMake und ein C++-Compiler (Windows: Visual Studio Build Tools).

```bash
python tools/build_dsftool.py --fetch-zlib   # DSFTool aus dem Original-Quellcode (MIT)
pnpm install
cd backend && uv sync && cd ..

pnpm dev:backend      # Terminal 1: Backend auf 127.0.0.1:8765, Token "dev"
pnpm dev:frontend     # Terminal 2: Oberfläche im Browser auf http://localhost:1420
# oder als Desktop-App (startet das Backend selbst):
pnpm tauri dev
```

Tests:

```bash
cd backend && uv run pytest -q && uv run ruff check . ../tools
pnpm typecheck
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
```

## Windows-Installer

Die CI (`.github/workflows/ci.yml`) baut bei jedem Push automatisch eine
`Setup.exe` (Artefakt *XPSB-Setup-windows-x64*): DSFTool.exe aus Quellcode,
Backend mit PyInstaller, Smoke-Test des fertigen Pakets, Tauri-NSIS-Installer
(Installation pro Benutzer, ohne Administratorrechte).

## Analyse und Editor testen (Meilenstein 2)

1. *Einstellungen*: Luftbildquelle „Automatisch“ lassen (Bayern/NRW) oder eine eigene
   WMS-Quelle eintragen und die Nutzungsrechte bestätigen. API-Key und Monatslimit prüfen.
2. Ein Gebiet in Bayern oder NRW zeichnen (z. B. ein Stadion), Kategorien wählen, bei
   *KI-Analyse* ein günstiges Modell und eine niedrige Kostengrenze (z. B. 1 USD) wählen.
3. Freigabe A lesen: Welche Daten werden geladen, was geht an die KI, Kostengrenze.
4. Ergebnis prüfen: Liste ↔ Karte (anklicken), Luftbild ein/aus, KI-Einschätzungen,
   Rückfragen beantworten, Einträge ändern, ausschließen, löschen oder neu einzeichnen.
   Die **KI-Objektsuche** findet im Luftbild, was in OSM fehlt (z. B. Tribünen) – die Funde
   erscheinen gestrichelt als Vorschläge und kommen erst nach deinem ✓ in den Bauplan.
5. Freigabe B → Bauplan zeigt die bestätigte Liste (gespeichert als `scene.geojson`).

Offline-Entwicklung ohne Internet: `XPSB_FIXTURES_DIR=backend/tests/fixtures/speedway`
liefert OSM-Daten und Luftbild aus Testdateien (siehe `tests/e2e/README.md`).

## Testszenerie in X-Plane 12 prüfen (Meilenstein 1)

1. App installieren und starten, unter *Einstellungen* den X-Plane-12-Ordner wählen.
2. Ein kleines Gebiet zeichnen (z. B. auf einem Flugplatz, damit du schnell dort bist).
3. Durch die Freigaben A–D klicken – das Paket landet in `Custom Scenery`.
4. X-Plane starten und zur Gebietsmitte fliegen. Erwartet:
   - **rot-weißer Würfel** (Text „XPSB“) steht auf dem Boden, alle Seiten sichtbar
   - **westlich** davon ein blaues Feld, Pfeil und „N“ zeigen **nach Norden**
   - **östlich** ein rot-weißes **Schachbrett** (Kachelmaterial)
5. Bitte melden, ob alles so aussieht – oder einen Screenshot schicken.

## Lizenz

MIT – siehe [`LICENSE`](LICENSE). Drittanbieter-Komponenten: [`third_party/`](third_party/).
Kartendaten © OpenStreetMap-Mitwirkende (ODbL).
