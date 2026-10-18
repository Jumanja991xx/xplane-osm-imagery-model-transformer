import { create } from "zustand";
import type { GeoJsonPolygon, JobEvent, JobView, Settings, SystemStatus } from "./api/client";

export type DrawMode = "none" | "polygon" | "rectangle";

interface AppState {
  aoi: GeoJsonPolygon | null;
  drawMode: DrawMode;
  clearRequest: number;
  job: JobView | null;
  events: JobEvent[];
  editingBrief: boolean;
  settings: Settings | null;
  system: SystemStatus | null;
  settingsOpen: boolean;

  setAoi: (aoi: GeoJsonPolygon | null) => void;
  setDrawMode: (mode: DrawMode) => void;
  requestClear: () => void;
  setJob: (job: JobView | null) => void;
  addEvent: (event: JobEvent) => void;
  setEditingBrief: (editing: boolean) => void;
  setSettings: (settings: Settings) => void;
  setSystem: (system: SystemStatus) => void;
  setSettingsOpen: (open: boolean) => void;
  reset: () => void;
}

export const useApp = create<AppState>((set) => ({
  aoi: null,
  drawMode: "none",
  clearRequest: 0,
  job: null,
  events: [],
  editingBrief: false,
  settings: null,
  system: null,
  settingsOpen: false,

  setAoi: (aoi) => set({ aoi }),
  setDrawMode: (drawMode) => set({ drawMode }),
  requestClear: () => set((s) => ({ clearRequest: s.clearRequest + 1, aoi: null, drawMode: "none" })),
  setJob: (job) =>
    set((s) => ({ job, events: job && s.job && job.id === s.job.id ? s.events : [] })),
  addEvent: (event) =>
    set((s) => (s.events.some((e) => e.seq === event.seq) ? s : { events: [...s.events, event] })),
  setEditingBrief: (editingBrief) => set({ editingBrief }),
  setSettings: (settings) => set({ settings }),
  setSystem: (system) => set({ system }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  reset: () =>
    set((s) => ({
      job: null,
      events: [],
      editingBrief: false,
      aoi: null,
      drawMode: "none",
      clearRequest: s.clearRequest + 1,
    })),
}));
