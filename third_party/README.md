# Drittanbieter-Komponenten

| Komponente | Lizenz | Verwendung |
|---|---|---|
| DSFTool (Laminar Research, xptools) | MIT | Kompiliert DSF-Text zu `.dsf`; aus Quellcode gebaut (`tools/build_dsftool.py`, gepinnter Commit) |
| zlib | zlib | Statisch in DSFTool |
| LZMA SDK | Public Domain | In DSFTool (7z-DSF lesen) |
| Ländergrenzen (Overture Maps Divisions, Release 2026-09-23.1) | ODbL 1.0 (© Overture Maps Foundation, © OpenStreetMap-Mitwirkende) | `backend/src/xpsb/imagery/coverage.json`: vereinfachte Grenzen der Länder/Bundesländer mit eingebauten Luftbildquellen, nur zur Wahl der Quelle |

Die vollständigen Lizenztexte liegen in `LICENSES/` und werden mit der App installiert
(`bin/LICENSES`). Eine automatisch erzeugte Liste aller Python- und npm-Abhängigkeiten
(THIRD_PARTY_NOTICES) folgt mit dem Release-Paket in Meilenstein 5.
