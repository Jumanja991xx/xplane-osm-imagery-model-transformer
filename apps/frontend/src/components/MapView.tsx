import { useEffect, useRef } from "react";
import {
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
  TerraDrawPolygonMode,
  TerraDrawRectangleMode,
  ValidateNotSelfIntersecting,
} from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
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
        ],
      });
      draw.start();
      draw.on("finish", (id) => {
        const feature = draw.getSnapshot().find((f) => f.id === id);
        if (!feature || feature.geometry.type !== "Polygon") return;
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
    });

    return () => {
      drawRef.current?.stop();
      drawRef.current = null;
      map.remove();
    };
  }, []);

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
