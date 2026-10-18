# Architektur – X-Plane AI-Scenery Builder

Stand: Plan v0.6 (freigegeben), Meilenstein 1.

## 1. Zweck und Umfang

Der Nutzer zeichnet auf einer Karte ein Gebiet (z. B. Rennstrecke, Stadion,
Verkehrsübungsplatz). Die App erzeugt daraus ein X-Plane-12-Szeneriepaket
(Overlay-DSF) mit **Objekten und den dazugehörigen Flächen**.

| Im Umfang | Nicht im Umfang |
|---|---|
| Gebäude, Tribünen, Gerüste & Stahlbau, Überdachungen & Zelte, Zäune/Mauern/Leitplanken, Masten | Gelände/Berge (kein Mesh, keine Höhendaten) |
| Flächen: Rasen, Asphalt, Beton, Kies, Tartan, Markierungen & Curbs | Wälder/Bäume, Gewässer, Straßennetze, Landnutzung |
| Optional: X-Plane-Standardobjekte im Gebiet ausblenden (Exclusions) | |

Zielplattform zuerst **Windows 10/11 (64-bit)**, macOS später. Verteilung als
kostenloses Community-Tool über nextsimflight.com.

## 2. Grundprinzip: Erst fragen, dann handeln

Kein Schritt mit Wirkung (Download, KI-Kosten, Dateien schreiben, X-Plane-Ordner
ändern) ohne ausdrückliche Freigabe. Jede Freigabe zeigt: **was passiert, was es
kostet, wohin geschrieben wird.**

```
① Bereich zeichnen          (nichts passiert)
② Auftrag klären            → FREIGABE A: Daten laden + analysieren
③ Analyse + Rückfragen      → FREIGABE B: Objekt-/Flächenliste bestätigt
④ Bauplan                   → FREIGABE C: Bauen
⑤ Installieren              → FREIGABE D: Kopieren nach Custom Scenery (mit Backup)
```

### Technische Absicherung (Backend)

- Zustandsmaschine pro Auftrag:
  `draft → clarified → analyzing → analyzed → planned → building → built → installing → installed`
  (+ `failed`, `cancelled`). Aktionen im falschen Zustand → HTTP 409.
- Jede Freigabe trägt den **Hash des Inhalts, den der Nutzer gesehen hat**
  (Freigabe-Zusammenfassung). Das Backend berechnet ihn neu; weicht er ab
  (etwas hat sich geändert), wird die Freigabe abgelehnt und neu gefragt.
- Alle Entscheidungen landen nachvollziehbar in `jobs/<id>/job.json`.
- Tests stellen sicher, dass jeder Endpunkt mit Wirkung ohne Freigabe verweigert.

## 3. Systemarchitektur

```
Tauri-App (Rust, dünn)  ──spawn (CREATE_NO_WINDOW)──►  Python-Backend (FastAPI)
  │  liest "XPSB_READY {port}" von stdout                  │  127.0.0.1:<zufälliger Port>
  │  invoke("get_backend_info") → {url, token}             │  Bearer-Token (aus Env XPSB_TOKEN)
  ▼                                                        │  REST: Befehle/Abfragen
WebView2: React + TS + Tailwind + MapLibre + Terra Draw ◄──┘  WS:   Fortschritt/Logs pro Auftrag
                                                           │
                                                           └─► DSFTool.exe (Subprozess, ohne Fenster)
```

- **Sidecar-Lebenszyklus:** Das Backend beendet sich, wenn stdin schließt
  (`--watch-stdin`, d. h. die App ist weg). Rust beendet es zusätzlich beim Schließen.
- **Sicherheit:** nur Loopback, Token in jeder Anfrage (WebSocket: Query-Parameter),
  CORS-Allowlist auf die Tauri-Origin.
- **Typen:** Pydantic-Modelle → OpenAPI → `openapi-typescript` → Frontend-Typen.

## 4. Ordnerstruktur

```
apps/frontend/          React-App (Vite)
apps/desktop/src-tauri/ Tauri-Shell (Rust), Windows-Installer (NSIS)
backend/src/xpsb/       Python-Backend
  api/        FastAPI-App, Auth, Routen
  jobs/       Zustandsmaschine, Freigaben, Events
  xplane/     OBJ8/POL/DSF-Text-Writer, DSFTool-Wrapper, Testszenerie
  settings.py, paths.py, secrets.py, xplane_detect.py
tools/dsftool/          Bau von DSFTool aus Laminars MIT-Quellcode (gepinnter Commit)
docs/                   Architektur, Format-Notizen, ADRs
```

Laufzeitdaten (nicht im Installationsordner):
`%APPDATA%\XPlaneAISceneryBuilder\` → `settings.json`, `jobs/<id>/`, `cache/`, `logs/`.

## 5. KI

- Rolle: „Art Director“ – liefert **geprüfte Parameter** (JSON-Schema), nie
  Geometrie oder X-Plane-Dateien. Geometrie erzeugen deterministische Generatoren.
- Standard: Claude-API (`claude-opus-5-5`) mit **eigenem API-Key des Nutzers**
  (Windows-Anmeldeinformationsverwaltung via `keyring`). Ohne Key: manueller Modus.
- Unsicherheit → Rückfrage an den Nutzer statt Annahme.
- Lokale Modelle (SAM 2, LaMa) optional, werden bei Bedarf nachgeladen.

## 6. Daten & Lizenzen

- OSM (ODbL): Attribution automatisch in jedem Paket.
- Satellitenbilder: nur offene Quellen als Vorlage + „eigene Quelle“ (Nutzer
  bestätigt Rechte). Keine Google/Bing/Esri-Vorlagen.
- Texturen: CC0.
- DSFTool/DDSTool: Laminar Research, MIT.

## 7. Roadmap

| | Inhalt |
|---|---|
| **M1** | Grundgerüst, 5-Schritte-Assistent, Zustandsmaschine + Freigaben, Settings (X-Plane-Pfad, DSFTool, API-Key), DE/EN, Testszenerie (Spike A) über die echte Pipeline, DSFTool aus Quellcode, Windows-CI-Build |
| **M2** | OSM + Bilddaten, KI-Analyse mit Rückfragen, Objekt-/Flächen-Editor |
| **M3** | Generatoren für Objekte und Flächen, Materialbibliothek, `.pol`/`.lin`, optional Ortho |
| **M4** | Bauplan, DSF-Export, Installation mit Backup, Exclusions |
| **M5** | Installer, Signatur, Download-Paket für nextsimflight.com, Update-Prüfung |
| später | macOS |
