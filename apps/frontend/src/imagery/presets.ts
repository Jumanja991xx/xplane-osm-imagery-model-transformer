import type { ImagerySettings } from "../api/client";

/** Ready-made settings for open aerial imagery that may be used for analysis and in a
 * shared scenery (with the attribution). Only sources whose terms allow that are
 * listed - no Google, Bing or Apple. Each preset fills the form; the user still
 * confirms the rights and saves. `test` is a place inside the coverage. */
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
    id: "basemap_at",
    label: "basemap.at Orthofoto",
    region: "Österreich",
    license: "CC BY (basemap.at)",
    test: [16.3725, 48.2083],
    settings: {
      source: "custom",
      custom_type: "xyz",
      custom_url: "https://{s}.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
      custom_subdomains: "maps,maps1,maps2,maps3,maps4",
      custom_max_zoom: 19,
      custom_attribution: "Datenquelle: basemap.at",
    },
  },
  {
    id: "swissimage",
    label: "swisstopo SWISSIMAGE",
    region: "Schweiz",
    license: "OGD swisstopo (freie Nutzung mit Quellenangabe)",
    test: [7.4474, 46.948],
    settings: {
      source: "custom",
      custom_type: "xyz",
      custom_url: "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg",
      custom_subdomains: "",
      custom_max_zoom: 20,
      custom_attribution: "© swisstopo",
    },
  },
  {
    id: "pdok",
    label: "PDOK Luchtfoto (actueel, hoge resolutie)",
    region: "Niederlande",
    license: "Beeldmateriaal.nl / PDOK (CC BY)",
    test: [4.8952, 52.3702],
    settings: {
      source: "custom",
      custom_type: "xyz",
      custom_url: "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg",
      custom_subdomains: "",
      custom_max_zoom: 19,
      custom_attribution: "Beeldmateriaal.nl / PDOK",
    },
  },
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
