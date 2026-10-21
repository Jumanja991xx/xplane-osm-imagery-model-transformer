import { invoke, isTauri } from "@tauri-apps/api/core";
import type { components } from "./schema";

type S = components["schemas"];
export type JobView = S["JobView"];
export type Brief = S["Brief"];
export type PendingGate = S["PendingGate"];
export type GateItem = S["GateItem"];
export type Settings = S["Settings"];
export type SystemStatus = S["SystemStatus"];
export type XPlaneCheck = S["XPlaneCheck"];
export type BlenderTest = S["BlenderTest"];
export type DsfToolStatus = S["DsfToolStatus"];
export type GeoJsonPolygon = S["GeoJsonPolygon"];
export type JobState = JobView["state"];
export type GateId = PendingGate["gate"];
export type ObjectCategory = S["ObjectCategory"];
export type SurfaceType = S["SurfaceType"];
export type ExclusionType = S["ExclusionType"];
export type SceneItem = S["SceneItem"];
export type ItemAttrs = S["ItemAttrs"];
export type ItemPatch = S["ItemPatch"];
export type NewItem = S["NewItem"];
export type Question = S["Question"];
export type Note = S["Note"];
export type ImageryInfo = S["ImageryInfo"];
export type ImagerySettings = S["ImagerySettings"];
export type SimilarEstimate = S["SimilarEstimate"];
export type PhotoTexture = S["PhotoTexture"];

export interface JobSummary {
  id: string;
  created_at: string;
  updated_at: string;
  state: JobState;
  package_name: string | null;
  area_km2: number;
  objects: number;
}

export interface JobEvent {
  seq: number;
  ts: string;
  type: "state" | "progress" | "log" | "error";
  state?: string | null;
  stage?: string | null;
  progress?: number | null;
  key?: string | null;
  params?: Record<string, string | number | null>;
  text?: string | null;
}

/** Error with an i18n key, as produced by the backend ({detail: {key, params}}). */
export class ApiError extends Error {
  constructor(
    public status: number,
    public key: string,
    public params: Record<string, unknown> = {},
  ) {
    super(key);
  }
}

interface Backend {
  baseUrl: string;
  token: string;
}

let backendPromise: Promise<Backend> | null = null;

/** In the desktop app the Rust shell knows port and token; in the browser (dev) we use env. */
export function backend(): Promise<Backend> {
  if (!backendPromise) {
    backendPromise = (async () => {
      if (isTauri()) {
        const info = await invoke<{ port: number; token: string }>("get_backend_info");
        return { baseUrl: `http://127.0.0.1:${info.port}`, token: info.token };
      }
      return {
        baseUrl: import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8765",
        token: import.meta.env.VITE_BACKEND_TOKEN ?? "dev",
      };
    })();
    backendPromise.catch(() => {
      backendPromise = null; // allow a retry, e.g. while the sidecar is still starting
    });
  }
  return backendPromise;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { baseUrl, token } = await backend();
  let res: Response;
  try {
    res = await fetch(baseUrl + path, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "error.backend_unreachable");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data?.detail;
    if (detail && typeof detail === "object" && "key" in detail) {
      throw new ApiError(res.status, detail.key, detail.params ?? {});
    }
    if (res.status === 422) {
      // FastAPI: [{loc: ["body", "package_name"], msg: ...}] -> name the field
      const fields = Array.isArray(detail)
        ? detail.map((d: { loc?: unknown[] }) => String((d.loc ?? []).slice(-1)[0] ?? "")).filter(Boolean)
        : [];
      if (fields.includes("package_name")) throw new ApiError(422, "error.invalid_package_name");
      if (fields.length) throw new ApiError(422, "error.validation_field", { field: fields.join(", ") });
      throw new ApiError(422, "error.validation", { detail: JSON.stringify(detail) });
    }
    throw new ApiError(res.status, "error.unexpected", { status: res.status });
  }
  return (await res.json()) as T;
}

export const api = {
  system: () => request<SystemStatus>("GET", "/api/system"),
  getSettings: () => request<Settings>("GET", "/api/settings"),
  putSettings: (s: Settings) => request<Settings>("PUT", "/api/settings", s),
  detectXPlane: () => request<XPlaneCheck[]>("POST", "/api/xplane/detect"),
  checkXPlane: (path: string) => request<XPlaneCheck>("POST", "/api/xplane/check", { path }),
  checkDsfTool: (path: string) => request<DsfToolStatus>("POST", "/api/dsftool/check", { path }),
  setApiKey: (apiKey: string) =>
    request<{ has_api_key: boolean }>("PUT", "/api/secrets/anthropic", { api_key: apiKey }),
  setEsriKey: (token: string) => request<S["KeyCheck"]>("PUT", "/api/secrets/esri", { token }),
  checkEsriKey: () => request<S["KeyCheck"]>("POST", "/api/secrets/esri/check"),
  testImagery: (imagery: ImagerySettings, lon?: number, lat?: number) =>
    request<S["SourceTest"]>("POST", "/api/imagery/test", { imagery, lon, lat }),
  testBlender: (path: string | null, device: "auto" | "cpu") =>
    request<S["BlenderTest"]>("POST", "/api/blender/test", { path, device }),
  deleteEsriKey: () => request<unknown>("DELETE", "/api/secrets/esri"),
  setMapillaryToken: (token: string) => request<unknown>("PUT", "/api/secrets/mapillary", { token }),
  deleteMapillaryToken: () => request<unknown>("DELETE", "/api/secrets/mapillary"),
  deleteApiKey: () => request<{ has_api_key: boolean }>("DELETE", "/api/secrets/anthropic"),

  createJob: (aoi: GeoJsonPolygon) => request<JobView>("POST", "/api/jobs", { aoi }),
  getJob: (id: string) => request<JobView>("GET", `/api/jobs/${id}`),
  putBrief: (id: string, brief: Brief) => request<JobView>("PUT", `/api/jobs/${id}/brief`, brief),
  postAnswers: (id: string, answers: Record<string, string>) =>
    request<JobView>("POST", `/api/jobs/${id}/answers`, answers),
  addItem: (id: string, item: NewItem) => request<JobView>("POST", `/api/jobs/${id}/items`, item),
  updateItem: (id: string, itemId: string, patch: ItemPatch) =>
    request<JobView>("PATCH", `/api/jobs/${id}/items/${itemId}`, patch),
  deleteItem: (id: string, itemId: string) =>
    request<JobView>("DELETE", `/api/jobs/${id}/items/${itemId}`),
  similarEstimate: (id: string, itemId: string) =>
    request<SimilarEstimate>("GET", `/api/jobs/${id}/similar/${itemId}`),
  findSimilar: (id: string, itemId: string, maxUsd: number) =>
    request<JobView>("POST", `/api/jobs/${id}/similar/${itemId}`, { max_usd: maxUsd }),
  approve: (id: string, gate: GateId, hash: string) =>
    request<JobView>("POST", `/api/jobs/${id}/gates/${gate}/approve`, { hash }),
  cancel: (id: string) => request<JobView>("POST", `/api/jobs/${id}/cancel`),
  reopen: (id: string, to: "analyzed" | "clarified") =>
    request<JobView>("POST", `/api/jobs/${id}/reopen`, { to }),
  renamePackage: (id: string, name: string) =>
    request<JobView>("POST", `/api/jobs/${id}/package-name`, { name }),
  listJobs: () => request<JobSummary[]>("GET", "/api/jobs"),
  designEstimate: (id: string, itemId: string) =>
    request<S["DesignEstimate"]>("GET", `/api/jobs/${id}/design/${itemId}`),
  redesign: (id: string, itemId: string, text: string, maxUsd: number) =>
    request<S["DesignOutcome"]>("POST", `/api/jobs/${id}/design/${itemId}`, { request: text, max_usd: maxUsd }),
  resetDesign: (id: string, itemId: string) => request<JobView>("DELETE", `/api/jobs/${id}/design/${itemId}`),
  addPhoto: (id: string, itemId: string, data: string) =>
    request<JobView>("POST", `/api/jobs/${id}/photos?item=${encodeURIComponent(itemId)}`, {
      data,
      rights_confirmed: true,
    }),
  deletePhoto: (id: string, photoId: string) => request<JobView>("DELETE", `/api/jobs/${id}/photos/${photoId}`),
  searchPhotos: (id: string, itemId: string) =>
    request<S["SearchResult"]>("GET", `/api/jobs/${id}/photo-search?item=${encodeURIComponent(itemId)}`),
  importPhoto: (id: string, candidateId: string) =>
    request<JobView>("POST", `/api/jobs/${id}/photo-search/${candidateId}/import`),
  skipBlender: (id: string) => request<JobView>("POST", `/api/jobs/${id}/skip-blender`),
  setTextures: (id: string, itemId: string, textures: PhotoTexture[]) =>
    request<JobView>("PUT", `/api/jobs/${id}/textures?item=${encodeURIComponent(itemId)}`, { textures }),
  adjustObject: (id: string, itemId: string, attrs: Partial<ItemAttrs>) =>
    request<JobView>("PATCH", `/api/jobs/${id}/objects/${itemId}`, { attrs }),
};

export async function jobEventsUrl(jobId: string): Promise<string> {
  const { baseUrl, token } = await backend();
  return `${baseUrl.replace(/^http/, "ws")}/ws/jobs/${jobId}?token=${encodeURIComponent(token)}`;
}

/** The job's aerial image as a blob URL (the endpoint needs the auth header). */
export async function imageryObjectUrl(jobId: string): Promise<string> {
  const { baseUrl, token } = await backend();
  const res = await fetch(`${baseUrl}/api/jobs/${jobId}/imagery`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, "error.no_imagery");
  return URL.createObjectURL(await res.blob());
}

/** An image the backend serves behind auth, as a blob URL (photos, search thumbnails). */
export async function authedImageUrl(path: string): Promise<string> {
  const { baseUrl, token } = await backend();
  const res = await fetch(baseUrl + path, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError(res.status, "error.photo_not_found");
  return URL.createObjectURL(await res.blob());
}

/** The straightened facade of a photo texture, as a blob URL (nothing is saved). */
export async function texturePreviewUrl(jobId: string, itemId: string, tex: PhotoTexture): Promise<string> {
  const { baseUrl, token } = await backend();
  const res = await fetch(`${baseUrl}/api/jobs/${jobId}/texture-preview?item=${encodeURIComponent(itemId)}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(tex),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail;
    throw new ApiError(res.status, detail?.key ?? "error.texture_corners", detail?.params ?? {});
  }
  return URL.createObjectURL(await res.blob());
}

export interface PreviewObject {
  item_id: string;
  stem: string;
  category: string;
  name?: string | null;
  status: "ok" | "error" | "skipped";
  message: string;
  triangles: number;
  offset: [number, number];
  has_mesh?: boolean;
  library?: { path: string; x: number; y: number; heading: number; size?: number[] }[];
}

export interface PreviewData {
  objects: PreviewObject[];
  surfaces: { category: string; rings: [number, number][][] }[];
  imagery: { sw: [number, number]; ne: [number, number] } | null;
  surface_mode?: string;
}

export interface PreviewLight {
  x: number;
  y: number;
  z: number;
  kind: string;
  color: [number, number, number];
  cd: number;
  dir: [number, number, number]; // local axes: x east, y north, z up
  cos: number; // cosine of half the beam angle
}

export interface PreviewMesh {
  positions: number[];
  normals: number[];
  uvs: number[];
  index: number[];
  lights?: PreviewLight[];
  lit?: boolean; // has a night texture
  alpha?: boolean; // see-through parts
}

async function authedFetch(path: string): Promise<Response> {
  const { baseUrl, token } = await backend();
  // no-store: single objects are rebuilt under the same name when adjusted
  const res = await fetch(baseUrl + path, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, "error.no_preview");
  return res;
}

export const preview = {
  scene: (jobId: string) => authedFetch(`/api/jobs/${jobId}/preview`).then((r) => r.json() as Promise<PreviewData>),
  mesh: (jobId: string, stem: string) =>
    authedFetch(`/api/jobs/${jobId}/preview/${stem}.json`).then((r) => r.json() as Promise<PreviewMesh>),
  textureUrl: async (jobId: string, stem: string) =>
    URL.createObjectURL(await (await authedFetch(`/api/jobs/${jobId}/preview/${stem}.png`)).blob()),
  /** Night texture of the object (lit windows, lamps). */
  litUrl: async (jobId: string, stem: string): Promise<string | null> => {
    try {
      return URL.createObjectURL(await (await authedFetch(`/api/jobs/${jobId}/preview/${stem}_lit.png`)).blob());
    } catch {
      return null;
    }
  },
  /** Normal map of the object, if it has one (objects built before it existed do not). */
  normalUrl: async (jobId: string, stem: string): Promise<string | null> => {
    try {
      return URL.createObjectURL(await (await authedFetch(`/api/jobs/${jobId}/preview/${stem}_nml.png`)).blob());
    } catch {
      return null;
    }
  },
};
