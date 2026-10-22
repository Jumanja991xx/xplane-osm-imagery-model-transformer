# X-Plane-Format-Notizen (verifiziert)

Geprüft gegen Laminar Researchs offiziellen Quellcode
[`X-Plane/xptools`](https://github.com/X-Plane/xptools) (MIT), Commit
`0137e80cdf03e8bb4cb58edb081f4ddf4a32c042`. Daraus werden DSFTool und WorldEditor
(WED) gebaut; WED erzeugt genau die Art Overlay, die wir erzeugen.

## DSF-Text (DSFTool)

Quelle: `src/DSFTools/README.dsf2text`, `src/WEDImportExport/WED_DSFExport.cpp`

- Kopf: `A` / `800` / `DSF2TEXT`
- Aufruf: `DSFTool --text2dsf <in.txt> <out.dsf>`, zurück: `--dsf2text`
- **`PROPERTY sim/west|east|north|south` müssen nach allen anderen
  `PROPERTY`/`DIVISIONS`/`HEIGHTS`-Zeilen kommen.**
- WED schreibt: `sim/west`, `sim/east`, `sim/north`, `sim/south`, `sim/planet earth`,
  `sim/creation_agent`, `sim/overlay 1` (ganzzahlige Tile-Grenzen).
- Alle Punkte müssen **innerhalb des 1°-Tiles** liegen, sonst lädt X-Plane die DSF
  nicht (WED klemmt an die Grenze).
- Definitionen: `OBJECT_DEF`, `POLYGON_DEF` (Index = Reihenfolge ab 0).
- Objekte: `OBJECT <idx> <lon> <lat> <heading>`; Varianten `OBJECT_AGL`/`OBJECT_MSL`
  mit zusätzlicher Höhe (DSFTool ≥ 2.2).
- Polygone: `BEGIN_POLYGON <idx> <param> [<coords>]`, `BEGIN_WINDING`,
  `POLYGON_POINT ...`, `END_WINDING`, `END_POLYGON`.
  - Facade: `param` = Höhe in Metern, coords = 2.
  - Draped Polygon mit Kachel-Textur: `param` = Ausrichtung (WED kodiert 1/128°).
  - Ortho/explizite UV: `param` = `65535`, coords = 4 (`lon lat s t`).
- Exclusions: `sim/exclude_obj|fac|for|bch|net|lin|pol|str`, Wert
  `west/south/east/north` (6 Nachkommastellen); neuere Versionen auch polygonal.
- Pfad: `Earth nav data/%+03d%+04d/%+03d%+04d.dsf` (10°-Ordner / 1°-Datei),
  z. B. `+40+010/+47+011.dsf`.
- DSFTool ist bei großen Dateien langsam (Minuten) → asynchron mit Statusanzeige.

## Draped Polygon (`.pol`)

Quelle: `src/WEDCore/WED_ResourceMgr.cpp` (`WritePol`), `WED_OrthoExport.cpp`

```
A
850
DRAPED_POLYGON

LOAD_CENTER <lat> <lon> <größe_m> <textur_px>
TEXTURE_NOWRAP <tex>     # Ortho; für Kachel-Material: TEXTURE <tex>
SCALE <s_m> <t_m>
NO_ALPHA                 # optional
LAYER_GROUP beaches 1    # WED-Standard für Orthos – aber: Spike A zeigte, dass
                         # vorhandene Flächen darüber liegen können → höhere Gruppe nötig
```

- WED begrenzt Ortho-Texturen auf **2048 px** (Zweierpotenzen).
- DDS: BC1 ohne Alpha, BC3 bei teilweiser Transparenz, mit Mipmaps.
- `scenery.txt` wird von WED **nicht** geschrieben → nicht nötig.

### Zeichenebenen (LAYER_GROUP), von unten nach oben

Quelle: `src/WEDMap/WED_PreviewLayer.cpp` (`kGroupNames`)

`terrain` → `beaches` → `unpaved_taxiways`/`unpaved_runways` → `shoulders` → `taxiways`
→ `runways` → `markings` → `airports` → `footprints` → `roads` → `objects` → `light_objects`

Unsere Flächen müssen über vorhandenen Flugplatz-/Szenerieflächen liegen (Spike A):
Testflächen nutzen `airports`. Für echte Flächen legen wir die Reihenfolge in M3 fest
(z. B. Rasen < Asphalt < Markierungen innerhalb einer Gruppe über Offsets).

## Facades (`.fac`)

- Offen (Zaun) vs. geschlossen (Gebäude) bestimmt die **`.fac`-Datei**
  (`RING`, Dach) – nicht die DSF (`WED_FacadePlacement::GetTopoMode`).
- DSF: Flächen-Fassaden dürfen **Löcher** haben (Innenhöfe): Außenring gegen den
  Uhrzeigersinn, Löcher im Uhrzeigersinn (`WED_PolygonWithHolesForPolygon`,
  `WED_DSFExport.cpp`), `param` = Höhe in m, 2 Koordinaten.
- Typ 1 (`A`/`800`/`FACADE`), Parser `WED_ResourceMgr::GetFac`, Geometrie
  `WED_FacadePreview.cpp`:
  - `TEXTURE`, `RING 1`, `TWO_SIDED 0`, `LOD <nah> <fern>` (erst danach Wände),
    `ROOF <s> <t>` (bis 4 Punkte, Dachtextur), `WALL <min_breite> <max_breite>
    <min_kurs> <max_kurs> <name>`, `SCALE <m pro Textur s> <m pro Textur t>`,
    `ROOF_SLOPE <grad> [SLANT]`, `BOTTOM|MIDDLE|TOP <t1> <t2>`,
    `LEFT|CENTER|RIGHT <s1> <s2>` (Texturanteile 0..1, oder Pixel mit `TEX_SIZE`).
  - Höhe aus der DSF: es werden so viele Geschosse gestapelt, dass die Höhe am
    besten passt (BOTTOM → MIDDLE wiederholt → TOP).
  - `ROOF_SLOPE`: das **letzte** TOP-Stück wird um den Winkel nach innen geklappt
    (Dachschräge/Mansarde); das flache Dach sitzt auf dem eingerückten Ring.

## Linien (`.lin`)

- Schlüsselwörter: `TEXTURE`, `SCALE`, `TEX_WIDTH`, `S_OFFSET`, `START_CAP`,
  `END_CAP`, `ALIGN`, `LAYER_GROUP`.

## DDSTool

- `DDSTool --png2dxt1|--png2dxt5 [--std_mips|--pre_mips] ...` (Laminar, MIT).

## OBJ8

- Achsen: +X = Ost, +Y = oben, +Z = Süd.
- **Dreiecke im Uhrzeigersinn** (vom Betrachter aus) sind Vorderseiten – in X-Plane 12
  verifiziert (Spike A, 18.10.2026). Gegen den Uhrzeigersinn zeigte das Innere des Würfels.
- Textur-Orientierung (t = 0 unten) bestätigt: Schrift stand richtig herum.
- Noch zu prüfen (M3): PBR-/Normal-Map-Attribute für X-Plane 12, Alpha-Cutout.
