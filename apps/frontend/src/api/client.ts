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
export type DsfToolStatus = S["DsfToolStatus"];
export type GeoJsonPolygon = S["GeoJsonPolygon"];
export type JobState = JobView["state"];
export type GateId = PendingGate["gate"];
export type ObjectCategory = S["ObjectCategory"];
export type SurfaceType = S["SurfaceType"];
export type ExclusionType = S["ExclusionType"];

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
    if (res.status === 422) throw new ApiError(422, "error.validation", { detail: JSON.stringify(detail) });
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
  deleteApiKey: () => request<{ has_api_key: boolean }>("DELETE", "/api/secrets/anthropic"),

  createJob: (aoi: GeoJsonPolygon) => request<JobView>("POST", "/api/jobs", { aoi }),
  getJob: (id: string) => request<JobView>("GET", `/api/jobs/${id}`),
  putBrief: (id: string, brief: Brief) => request<JobView>("PUT", `/api/jobs/${id}/brief`, brief),
  postAnswers: (id: string, answers: Record<string, string>) =>
    request<JobView>("POST", `/api/jobs/${id}/answers`, answers),
  approve: (id: string, gate: GateId, hash: string) =>
    request<JobView>("POST", `/api/jobs/${id}/gates/${gate}/approve`, { hash }),
  cancel: (id: string) => request<JobView>("POST", `/api/jobs/${id}/cancel`),
};

export async function jobEventsUrl(jobId: string): Promise<string> {
  const { baseUrl, token } = await backend();
  return `${baseUrl.replace(/^http/, "ws")}/ws/jobs/${jobId}?token=${encodeURIComponent(token)}`;
}
