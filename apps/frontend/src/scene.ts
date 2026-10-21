import type { ObjectCategory, SceneItem, SurfaceType } from "./api/client";
import type { ItemDraft } from "./store";

export const OBJECT_CATEGORIES: ObjectCategory[] = [
  "buildings",
  "grandstands",
  "scaffolding",
  "canopies",
  "barriers",
  "masts",
  "monuments",
];
export const SURFACE_TYPES: SurfaceType[] = ["grass", "asphalt", "concrete", "gravel", "dirt", "tartan", "markings"];

export const CATEGORY_COLORS: Record<ObjectCategory | SurfaceType, string> = {
  buildings: "#e11d48",
  grandstands: "#9333ea",
  scaffolding: "#f97316",
  canopies: "#0891b2",
  barriers: "#facc15",
  masts: "#f43f5e",
  monuments: "#a16207",
  grass: "#22c55e",
  asphalt: "#52525b",
  concrete: "#a1a1aa",
  gravel: "#d6d3d1",
  dirt: "#b45309",
  tartan: "#dc2626",
  markings: "#ffffff",
};

export function categoryLabelKey(item: Pick<SceneItem, "kind" | "category">): string {
  return item.kind === "object" ? `category.${item.category}` : `surface.${item.category}`;
}

export function draftFor(kind: "object" | "surface", category: ObjectCategory | SurfaceType): ItemDraft {
  const mode = category === "barriers" ? "linestring" : category === "masts" || category === "monuments" ? "point" : "polygon";
  return { kind, category, mode };
}

/** [west, south, east, north] of any GeoJSON geometry. */
export function bbox(geometry: SceneItem["geometry"]): [number, number, number, number] {
  let w = Infinity,
    s = Infinity,
    e = -Infinity,
    n = -Infinity;
  const visit = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") {
      const [x, y] = c as number[];
      w = Math.min(w, x);
      e = Math.max(e, x);
      s = Math.min(s, y);
      n = Math.max(n, y);
    } else if (Array.isArray(c)) c.forEach(visit);
  };
  visit((geometry as { coordinates: unknown }).coordinates);
  return [w, s, e, n];
}

/** Block heights for the 3D preview when neither OSM nor the AI knows one. */
export const DEFAULT_HEIGHTS: Record<ObjectCategory, number> = {
  buildings: 8,
  grandstands: 10,
  scaffolding: 6,
  canopies: 5,
  barriers: 2,
  masts: 20,
  monuments: 6,
};
