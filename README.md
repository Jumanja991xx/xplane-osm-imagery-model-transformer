# X-Plane AI-Scenery Builder

Desktop-App (Windows), die aus einem auf der Karte gezeichneten Gebiet ein
X-Plane-12-Szeneriepaket mit **Objekten und den dazugehörigen Flächen** erzeugt –
Tribünen, Gebäude, Gerüste, Rasen, Asphalt usw. Kostenlos für die Community.

**Grundprinzip:** Die App fragt vor jedem Schritt mit Wirkung (Download, KI-Kosten,
Dateien schreiben, X-Plane-Ordner ändern) und zeigt dabei, *was passiert, was es kostet
und wohin geschrieben wird*.

> **Status: Meilenstein 1 (Vorschau).** Der komplette Ablauf mit allen Freigaben
> funktioniert. Die Analyse ist noch ein Platzhalter; gebaut wird eine **Testszenerie**
> (Würfel + zwei Testflächen), mit der wir die Technik in X-Plane prüfen.
> Echte Objekterkennung (OSM, Satellitenbild, KI) folgt in Meilenstein 2.

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
