# X-Plane AI-Scenery Builder

> 🇩🇪 Deutsche Version: [README.de.md](README.de.md)

A free Windows desktop app for the flight-sim community: **draw an area on the map and get
an X-Plane 12 scenery package that looks like the real place** – stadiums, palaces,
churches, halls, houses, stands, fences and the ground surfaces around them.

The app finds out *what* is in the area (OpenStreetMap, aerial imagery, Wikidata/Wikipedia,
street-level photos), works out *how it looks*, builds a 3D model of every object, packs
everything into an overlay scenery and installs it into `Custom Scenery`.

**Guiding principle:** before every step that has an effect – downloading data, spending
money on AI, writing files, changing your X-Plane folder – the app stops and shows *what will
happen, what it costs and where it writes*. Nothing happens without your approval.

> **Status: milestone 3 – preview version.** The whole flow works end to end and produces
> real packages, but this is still early software. Feedback from X-Plane is very welcome
> (see [What to check in X-Plane](#what-to-check-in-x-plane)).

## How it works

1. **Area** – draw a polygon on the map.
2. **Brief** – choose what to build, the level of detail, whether and how much AI may be
   used (with a hard cost limit), and the package name.
3. **Analysis** – OpenStreetMap data and aerial imagery are loaded; the AI (optional) checks
   the image for things missing in OSM (e.g. stands), researches important objects and
   collects photos. You review everything in a list and on the map, fix heights, colours,
   roof shapes, add or remove objects, draw new ones.
4. **Plan** – every object is built and shown in a **3D preview** (day and night). The *AI
   architect* rebuilds important objects individually; you can ask for changes in your own
   words ("the roof should cover the whole stand", "add a clock tower").
5. **Install** – the package is compiled with Laminar's DSFTool, checked and copied into
   `Custom Scenery` (an existing folder is backed up first).

## Features

- **Building kit with real depth** – facades with windows set into the wall (reveals,
  panes, doors), cornices, string courses, pilasters, rusticated ground floors, mansard and
  hipped roofs, balustrades with statues, chimneys, courtyards; normal maps for joints,
  tiles and ribs.
- **Stadium kit** – two tiers with seat rows in the real seat colour, advertising fascia,
  stair aisles, lamella facade, translucent inner roof ring, girders, floodlights.
- **Facades from photos** – mark the four corners of a facade on a photo; the app
  straightens it and paints it onto the model as a texture, the way hand-made sceneries
  do. For repeating facades (stadiums, offices) a small sample is enough. Only photos whose
  licence allows sharing are used (own photos, CC0, CC BY, CC BY-SA, …); photographers are
  named in the package README.
- **Night** – lit windows (`TEXTURE_LIT`), floodlit landmarks, stadium floodlights, street
  lamps, using X-Plane 12's own lights.
- **Levels of detail** – real windows close up, painted facades further away.
- **Waving flags** that turn with the wind.
- **X-Plane library objects** – lamps, trees, vehicles and the like are placed from the
  libraries you have installed instead of being modelled.
- **Blender (optional)** – if you have [Blender](https://www.blender.org) installed, the app
  uses it at build time to bake soft shading (ambient occlusion) and fine facade detail
  (window surrounds, pediments, dentils) into the textures – detail X-Plane does not have to
  draw. Blender runs invisibly with a fixed script that ships with the app; the AI never
  writes code that runs on your computer.
- **Safe AI scripts** – AI-written build scripts run in a sandbox (Starlark: no files, no
  network, time and triangle limits). They can only describe geometry.
- **Memory** – corrections you make in an area are remembered for the next job there.
- German and English interface.

## Requirements

| | |
|---|---|
| **Required** | Windows 10/11 (64-bit), X-Plane 12 |
| **Optional: AI** | your own [Anthropic API key](https://console.anthropic.com) (Claude). You pay Anthropic directly for what you use; the app shows an estimate before every AI step and never exceeds the limits you set (per job and per month). Without a key the app works with its default build scripts. |
| **Optional: street photos** | a free [Mapillary](https://www.mapillary.com/developer) token (the open alternative to Street View) |
| **Optional: worldwide aerial imagery** | your own [ArcGIS](https://developers.arcgis.com) key for Esri World Imagery – used **only for recognition**, it never goes into a package |
| **Optional: shading** | [Blender](https://www.blender.org) 3.x or newer (4.x/5.x recommended); a graphics card makes it much faster |

## Installation

There is no official release yet. Every push builds a Windows installer automatically:

1. Open the [latest successful CI run](https://github.com/Jumanja991xx/xplane-osm-imagery-model-transformer/actions/workflows/ci.yml?query=is%3Asuccess)
   (you need to be logged in to GitHub to download artifacts).
2. Download the artifact **XPSB-Setup-windows-x64**, unzip it and run the setup.
   It installs per user, no administrator rights needed.
3. Start the app, open **Settings** and select your X-Plane 12 folder. Add the optional keys
   if you want to use them. The settings header shows which build is installed.

## Data sources, licences and privacy

- **Map data** © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright) –
  credited in every package.
- **Aerial imagery built in:** open government imagery, chosen automatically by the area's
  real borders and credited in the package – Bavaria (DOP40), North Rhine-Westphalia (DOP),
  Switzerland (swisstopo SWISSIMAGE), Austria (basemap.at), Netherlands (PDOK), France
  (IGN BD ORTHO), Spain (IGN PNOA), USA (USGS NAIP Plus, public domain) and Japan (GSI,
  recognition only – its terms do not allow passing the imagery on). If a server fails,
  the next matching source is tried. You can add your own WMS/XYZ source if you have the
  right to use it. Google, Bing and Apple imagery are not built in. Country borders for
  choosing the source: © Overture Maps Foundation / OpenStreetMap contributors (ODbL).
- **Esri World Imagery** (with your own key) is used for recognition only and is never put
  into a package.
- **Photos** come from your own uploads, Wikimedia Commons, Panoramax and Mapillary. They go
  to the AI as reference; a photo only becomes part of a package when you (or the AI) cut a
  facade out of it **and** its licence allows sharing.
- **Keys and tokens** are stored only in the Windows Credential Manager – never in files,
  logs or job folders, and they are never shown again after saving.
- Everything runs on your computer. Data leaves it only for the sources above and, if you
  enable it, for the Anthropic API.

## What to check in X-Plane

These things cannot be verified without the simulator – reports and screenshots help a lot:

- Do joints, window frames and pediments look **raised** (correct) or **pressed in**?
  (normal-map orientation)
- Are floodlights, street lamps and lit windows too bright or too dark at night?
- Do flags fly **downwind**?
- Order in `scenery_packs.ini`: packages from this app should be **above** autogen packs such
  as OSM2XP or other landmark packs, otherwise buildings may appear twice.

## Development

```
apps/frontend/          React + TypeScript + Tailwind + MapLibre + Terra Draw + three.js
apps/desktop/src-tauri/ Tauri shell (Rust): starts the backend invisibly, NSIS installer
backend/                Python backend (FastAPI): approvals, AI, build kit, X-Plane writers
tools/                  build DSFTool from Laminar's source, packaging, smoke test
tests/e2e/              browser test of the complete wizard (offline fixtures, fake AI)
docs/                   architecture and verified X-Plane format notes
```

Requirements: Python ≥ 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm,
Rust (stable), CMake and a C++ compiler (Windows: Visual Studio Build Tools).

```bash
python tools/build_dsftool.py --fetch-zlib   # DSFTool from Laminar's original source (MIT)
pnpm install
cd backend && uv sync && cd ..

pnpm dev:backend      # terminal 1: backend on 127.0.0.1:8765, token "dev"
pnpm dev:frontend     # terminal 2: UI in the browser on http://localhost:1420
# or as the desktop app (starts the backend itself):
pnpm tauri dev
```

Tests:

```bash
cd backend && uv run pytest -q && uv run ruff check . ../tools
pnpm typecheck
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
```

Offline development without internet: `XPSB_FIXTURES_DIR=backend/tests/fixtures/speedway`
serves OSM data and imagery from test files, `XPSB_FAKE_AI` simulates the AI (no key, no
cost) – see [`tests/e2e/README.md`](tests/e2e/README.md).

The CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the tests on Linux and
Windows, builds DSFTool.exe from source, packages the backend with PyInstaller, smoke-tests
the packaged backend with a real build and creates the Tauri NSIS installer.

More: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ·
[`docs/xplane-format-notes.md`](docs/xplane-format-notes.md)

## Licence

MIT – see [`LICENSE`](LICENSE). Third-party components: [`third_party/`](third_party/).
Map data © OpenStreetMap contributors (ODbL).

X-Plane is a trademark of Laminar Research. This project is not affiliated with Laminar
Research, Anthropic, Esri, Mapillary or Blender.
