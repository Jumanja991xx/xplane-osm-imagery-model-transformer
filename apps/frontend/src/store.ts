import { create } from "zustand";
import type {
  GeoJsonPolygon,
  JobEvent,
  JobView,
  ObjectCategory,
  Settings,
  SurfaceType,
  SystemStatus,
} from "./api/client";

export type DrawMode = "none" | "polygon" | "rectangle" | "linestring" | "point";

/** A new object/surface the user is drawing in the editor. */
export interface ItemDraft {
  kind: "object" | "surface";
  category: ObjectCategory | SurfaceType;
  mode: "polygon" | "linestring" | "point";
}

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
  jobsOpen: boolean;
  selectedItem: string | null;
  imageryVisible: boolean;
  view3d: boolean;
  itemDraft: ItemDraft | null;

  setAoi: (aoi: GeoJsonPolygon | null) => void;
  setDrawMode: (mode: DrawMode) => void;
  requestClear: () => void;
  setJob: (job: JobView | null) => void;
  addEvent: (event: JobEvent) => void;
  setEditingBrief: (editing: boolean) => void;
  setSettings: (settings: Settings) => void;
  setSystem: (system: SystemStatus) => void;
  setSettingsOpen: (open: boolean) => void;
  setJobsOpen: (open: boolean) => void;
  selectItem: (id: string | null) => void;
  setImageryVisible: (visible: boolean) => void;
  setView3d: (on: boolean) => void;
  startItemDraft: (draft: ItemDraft | null) => void;
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
  jobsOpen: false,
  selectedItem: null,
  imageryVisible: true,
  view3d: false,
  itemDraft: null,

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
  setJobsOpen: (jobsOpen) => set({ jobsOpen }),
  selectItem: (selectedItem) => set({ selectedItem }),
  setImageryVisible: (imageryVisible) => set({ imageryVisible }),
  setView3d: (view3d) => set({ view3d }),
  startItemDraft: (itemDraft) => set({ itemDraft, drawMode: itemDraft ? itemDraft.mode : "none" }),
  reset: () =>
    set((s) => ({
      job: null,
      selectedItem: null,
      itemDraft: null,
      events: [],
      editingBrief: false,
      aoi: null,
      drawMode: "none",
      clearRequest: s.clearRequest + 1,
    })),
}));
