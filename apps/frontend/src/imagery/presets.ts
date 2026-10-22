import type { ImagerySettings } from "../api/client";

/** Ready-made settings for imagery that is not built in as a source of its own.
 * Only sources whose terms allow the use - no Google, Bing or Apple. Each preset
 * fills the form; the user still confirms the rights and saves. `test` is a place
 * inside the coverage. (Open national imagery - Bavaria, NRW, Switzerland, Austria,
 * Netherlands, France, Spain, USA, Japan - is built in and chosen automatically.) */
export interface ImageryPreset {
  id: string;
  label: string;
  region: string;
  license: string;
  test: [number, number]; // lon, lat
  settings: Partial<ImagerySettings>;
}

export const IMAGERY_PRESETS: ImageryPreset[] = [
  {
    // Not an own source: switches to the built-in Esri access with the user's key
    // (recognition and AI only, never in the package - Esri's terms).
    id: "esri",
    label: "Esri World Imagery (eigener ArcGIS-Key)",
    region: "weltweit",
    license: "Esri (nur Erkennung, nicht im Paket)",
    test: [11.5, 48.1],
    settings: { source: "esri" },
  },
];
