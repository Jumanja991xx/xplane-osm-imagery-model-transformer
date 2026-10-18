import { useEffect, useRef, useState } from "react";
import {
  type ExpressionSpecification,
  type GeoJSONSource,
  type ImageSource,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
  setWorkerUrl,
  type StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 derives its worker URL from import.meta.url, which breaks once Vite
// bundles it. Let Vite bundle the worker itself and hand MapLibre the result.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import {
  TerraDraw,
  TerraDrawLineStringMode,
  TerraDrawPointMode,
  TerraDrawPolygonMode,
  TerraDrawRectangleMode,
  ValidateNotSelfIntersecting,
} from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import { api, ApiError, imageryObjectUrl, type JobView } from "../api/client";
import { bbox, CATEGORY_COLORS } from "../scene";
import { useApp } from "../store";

// OpenStreetMap standard map in the German style (openstreetmap.de): shows more
// detail (buildings, tracks, names) than vector styles at the zoom levels we use.
const BASEMAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.de/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>-Mitwirkende',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

// Used when the basemap cannot be loaded (offline, blocked): drawing still works.
const BLANK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#e4e4e7" } }],
};

function initialStyle(): string | StyleSpecification {
  const configured = import.meta.env.VITE_BASEMAP_STYLE;
  if (configured === "blank") return BLANK_STYLE;
  return configured || BASEMAP_STYLE;
}

setWorkerUrl(maplibreWorkerUrl);

const AOI_STYLE = {
  fillColor: "#2b8ad8",
  fillOpacity: 0.15,
  outlineColor: "#1f73bd",
  outlineWidth: 2,
} as const;

export function MapView() {
  const container = useRef<HTMLDivElement>(null);
  const drawRef = useRef<TerraDraw | null>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const drawMode = useApp((s) => s.drawMode);
  const clearRequest = useApp((s) => s.clearRequest);

  useEffect(() => {
    if (!container.current) return;
    const map = new MapLibreMap({
      container: container.current,
      style: initialStyle(),
      center: [10.4, 51.1],
      zoom: 5,
      attributionControl: { compact: true },
    });
    if (import.meta.env.DEV) {
      // Lets browser tests move the map without a place search (arrives in M2).
      (window as unknown as { __xpsbMap?: MapLibreMap }).__xpsbMap = map;
    }
    let fellBack = false;
    map.on("error", () => {
      if (!fellBack && !map.isStyleLoaded()) {
        fellBack = true;
        map.setStyle(BLANK_STYLE);
      }
    });
    map.addControl(new NavigationControl({ visualizePitch: false }), "top-right");
    map.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");

    map.once("load", () => {
      const draw = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map }),
        modes: [
          new TerraDrawPolygonMode({
            styles: AOI_STYLE,
            validation: (feature) => ValidateNotSelfIntersecting(feature),
          }),
          new TerraDrawRectangleMode({ styles: AOI_STYLE }),
          new TerraDrawLineStringMode({ styles: { lineStringColor: "#0ea5e9", lineStringWidth: 3 } }),
          new TerraDrawPointMode({ styles: { pointColor: "#0ea5e9", pointWidth: 6 } }),
        ],
      });
      draw.start();
      draw.on("finish", (id) => {
        const feature = draw.getSnapshot().find((f) => f.id === id);
        if (!feature) return;
        const itemDraft = useApp.getState().itemDraft;
        if (itemDraft) {
          void addDrawnItem(feature.geometry as unknown as Record<string, unknown>);
          draw.clear();
          return;
        }
        if (feature.geometry.type !== "Polygon") return;
        // Exactly one area per job: drop anything drawn before.
        const others = draw.getSnapshot().filter((f) => f.id !== id).map((f) => f.id!);
        if (others.length) draw.removeFeatures(others);
        useApp.getState().setAoi({
          type: "Polygon",
          coordinates: feature.geometry.coordinates as number[][][],
        });
        useApp.getState().setDrawMode("none");
      });
      drawRef.current = draw;
      const mode = useApp.getState().drawMode;
      if (mode !== "none") draw.setMode(mode);
      setupSceneLayers(map);
      setMap(map);
    });

    return () => {
      drawRef.current?.stop();
      drawRef.current = null;
      setMap(null);
      map.remove();
    };
  }, []);

  useSceneSync(map);

  useEffect(() => {
    const draw = drawRef.current;
    if (!draw) return;
    if (drawMode === "none") {
      draw.setMode("static");
    } else {
      draw.clear();
      draw.setMode(drawMode);
    }
  }, [drawMode]);

  useEffect(() => {
    drawRef.current?.clear();
  }, [clearRequest]);

  return <div ref={container} className="h-full w-full" data-testid="map" />;
}

// ---- analysed objects and surfaces ------------------------------------------------

const SCENE_LAYERS = ["scene-fill", "scene-line", "scene-point"];
const EMPTY = { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection;

const colorExpr = [
  "match",
  ["get", "category"],
  ...Object.entries(CATEGORY_COLORS).flat(),
  "#0ea5e9",
] as unknown as ExpressionSpecification;

async function addDrawnItem(geometry: Record<string, unknown>) {
  const { job, itemDraft, setJob, selectItem, startItemDraft } = useApp.getState();
  startItemDraft(null);
  if (!job || !itemDraft) return;
  try {
    const updated = await api.addItem(job.id, {
      kind: itemDraft.kind,
      category: itemDraft.category,
      geometry: { type: geometry.type, coordinates: geometry.coordinates },
    });
    setJob(updated);
    const items = updated.analysis?.items ?? [];
    selectItem(items[items.length - 1]?.id ?? null);
  } catch (e) {
    window.alert(e instanceof ApiError ? e.key : String(e));
  }
}

function setupSceneLayers(map: MapLibreMap) {
  // Below Terra Draw's layers, so a shape being drawn stays visible on top.
  const before = map.getStyle().layers.find((l) => l.id.startsWith("td-"))?.id;
  map.addSource("aoi", { type: "geojson", data: EMPTY });
  map.addSource("scene", { type: "geojson", data: EMPTY });
  map.addLayer(
    {
      id: "scene-fill",
      type: "fill",
      source: "scene",
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: {
        "fill-color": colorExpr,
        "fill-opacity": ["case", ["get", "included"], ["case", ["==", ["get", "kind"], "surface"], 0.35, 0.45], 0.06],
      },
    },
    before,
  );
  map.addLayer(
    {
      id: "scene-line",
      type: "line",
      source: "scene",
      filter: ["!=", ["geometry-type"], "Point"],
      paint: {
        "line-color": colorExpr,
        "line-width": ["case", ["==", ["get", "kind"], "object"], 2, 1.5],
        "line-opacity": ["case", ["get", "included"], 1, 0.35],
      },
    },
    before,
  );
  map.addLayer(
    {
      id: "scene-point",
      type: "circle",
      source: "scene",
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-color": colorExpr,
        "circle-radius": 6,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.5,
        "circle-opacity": ["case", ["get", "included"], 1, 0.35],
      },
    },
    before,
  );
  map.addLayer(
    {
      id: "scene-selected",
      type: "line",
      source: "scene",
      filter: ["==", ["get", "itemId"], ""],
      paint: { "line-color": "#0ea5e9", "line-width": 4 },
    },
    before,
  );
  map.addLayer(
    {
      id: "scene-selected-point",
      type: "circle",
      source: "scene",
      filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "itemId"], ""]],
      paint: { "circle-radius": 10, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#0ea5e9", "circle-stroke-width": 3 },
    },
    before,
  );
  map.addLayer(
    {
      id: "aoi-outline",
      type: "line",
      source: "aoi",
      paint: { "line-color": "#1f73bd", "line-width": 2, "line-dasharray": [2, 2] },
    },
    before,
  );

  map.on("click", (e) => {
    const { itemDraft, drawMode, selectItem } = useApp.getState();
    if (itemDraft || drawMode !== "none") return;
    const hits = map.queryRenderedFeatures(e.point, { layers: SCENE_LAYERS });
    if (!hits.length) return;
    // Objects sit on surfaces: prefer them, then the smallest hit.
    const best = hits.find((f) => f.properties.kind === "object") ?? hits[0];
    selectItem(String(best.properties.itemId));
  });
  for (const layer of SCENE_LAYERS) {
    map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
  }
}

function sceneData(job: JobView | null): GeoJSON.FeatureCollection {
  const items = job?.analysis?.items ?? [];
  return {
    type: "FeatureCollection",
    features: items.map((it) => ({
      type: "Feature",
      geometry: it.geometry as unknown as GeoJSON.Geometry,
      properties: { itemId: it.id, kind: it.kind, category: it.category, included: it.included ?? true },
    })),
  };
}

function useSceneSync(map: MapLibreMap | null) {
  const job = useApp((s) => s.job);
  const selected = useApp((s) => s.selectedItem);
  const imageryVisible = useApp((s) => s.imageryVisible);
  const imagery = job?.analysis?.imagery ?? null;
  const fittedJob = useRef<string | null>(null);

  // Items and area outline.
  useEffect(() => {
    if (!map) return;
    (map.getSource("scene") as GeoJSONSource | undefined)?.setData(sceneData(job));
    (map.getSource("aoi") as GeoJSONSource | undefined)?.setData(
      job ? { type: "Feature", geometry: job.aoi as GeoJSON.Polygon, properties: {} } : EMPTY,
    );
    if (job?.analysis && fittedJob.current !== job.id) {
      fittedJob.current = job.id;
      const [w, s, e, n] = bbox(job.aoi);
      map.fitBounds([w, s, e, n], { padding: 40, duration: 600 });
    }
    if (!job) fittedJob.current = null;
  }, [map, job]);

  // Selection highlight; bring the item into view when it is outside.
  useEffect(() => {
    if (!map || !map.getLayer("scene-selected")) return;
    map.setFilter("scene-selected", ["==", ["get", "itemId"], selected ?? ""]);
    map.setFilter("scene-selected-point", [
      "all",
      ["==", ["geometry-type"], "Point"],
      ["==", ["get", "itemId"], selected ?? ""],
    ]);
    const item = useApp.getState().job?.analysis?.items?.find((i) => i.id === selected);
    if (item) {
      const [w, s, e, n] = bbox(item.geometry);
      const view = map.getBounds();
      if (!view.contains([w, s]) || !view.contains([e, n])) {
        map.fitBounds(new LngLatBounds([w, s], [e, n]), { padding: 120, maxZoom: 18, duration: 500 });
      }
    }
  }, [map, selected]);

  // Aerial image of the job, under everything else.
  const imageryKey = imagery && job ? `${job.id}:${imagery.west}:${imagery.north}` : null;
  useEffect(() => {
    if (!map || !imagery || !job) return;
    let url: string | null = null;
    let cancelled = false;
    void imageryObjectUrl(job.id)
      .then((objectUrl) => {
        url = objectUrl;
        if (cancelled) return URL.revokeObjectURL(objectUrl);
        const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
          [imagery.west, imagery.north],
          [imagery.east, imagery.north],
          [imagery.east, imagery.south],
          [imagery.west, imagery.south],
        ];
        const source = map.getSource("imagery") as ImageSource | undefined;
        if (source) {
          source.updateImage({ url: objectUrl, coordinates });
        } else {
          map.addSource("imagery", { type: "image", url: objectUrl, coordinates });
          map.addLayer(
            {
              id: "imagery",
              type: "raster",
              source: "imagery",
              layout: { visibility: useApp.getState().imageryVisible ? "visible" : "none" },
            },
            "scene-fill",
          );
        }
      })
      .catch(() => {
        /* no image: the map works without it */
      });
    return () => {
      cancelled = true;
      if (map.getLayer("imagery")) map.removeLayer("imagery");
      if (map.getSource("imagery")) map.removeSource("imagery");
      if (url) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, imageryKey]);

  useEffect(() => {
    if (map?.getLayer("imagery")) {
      map.setLayoutProperty("imagery", "visibility", imageryVisible ? "visible" : "none");
    }
  }, [map, imageryVisible]);
}
