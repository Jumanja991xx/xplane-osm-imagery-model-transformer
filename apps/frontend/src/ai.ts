import { useEffect, useState } from "react";
import { api, type AiModelEntry, type SystemStatus } from "./api/client";
import type { Translate } from "./i18n";

export type Provider = "anthropic" | "google";

export const providerOf = (model: string): Provider => (model.startsWith("gemini-") ? "google" : "anthropic");

export function hasKeyFor(model: string | undefined | null, system: SystemStatus | null | undefined): boolean {
  if (!model || !system) return false;
  return providerOf(model) === "google" ? Boolean(system.has_gemini_key) : Boolean(system.has_api_key);
}

/** "gemini-3.8-flash" -> "Gemini 3.8 Flash"; Claude models have their own names. */
export function modelName(t: Translate, id: string): string {
  const key = `model.${id}`;
  const known = t(key);
  if (known !== key) return known;
  return id
    .split("-")
    .map((w) => (/^\d/.test(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

export function modelLabel(t: Translate, m: AiModelEntry): string {
  const notes = [m.tier ? t(`model.tier.${m.tier}`) : "", m.note ? t(`model.note.${m.note}`) : ""].filter(Boolean);
  return `${modelName(t, m.id)}${notes.length ? ` – ${notes.join(", ")}` : ""}`;
}

let cached: AiModelEntry[] | null = null;
const extra: AiModelEntry[] = [];

/** The models the app knows plus the Gemini models loaded from Google this session. */
export function useAiModels(): [AiModelEntry[], (more: AiModelEntry[]) => void] {
  const [models, setModels] = useState<AiModelEntry[]>(cached ? [...cached, ...extra] : []);
  useEffect(() => {
    if (cached) return;
    void api
      .aiModels()
      .then((list) => {
        cached = list;
        setModels([...list, ...extra]);
      })
      .catch(() => setModels([]));
  }, []);
  const add = (more: AiModelEntry[]) => {
    const known = new Set([...(cached ?? []), ...extra].map((m) => m.id));
    extra.push(...more.filter((m) => !known.has(m.id)));
    setModels([...(cached ?? []), ...extra]);
  };
  return [models, add];
}

/** <option>s grouped by provider; models whose key is missing are disabled. */
export function modelGroups(models: AiModelEntry[], current: string | undefined) {
  const list = current && !models.some((m) => m.id === current)
    ? [...models, { id: current, provider: providerOf(current), price_in: 0, price_out: 0, note: "" } as AiModelEntry]
    : models;
  return (["anthropic", "google"] as const).map((p) => ({ provider: p, models: list.filter((m) => m.provider === p) }));
}
