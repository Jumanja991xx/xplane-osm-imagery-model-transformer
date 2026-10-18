import { useState } from "react";
import { SURFACE_TYPES } from "../../scene";
import {
  api,
  ApiError,
  type Brief,
  type ExclusionType,
  type ObjectCategory,
  type SurfaceType,
} from "../../api/client";
import { useT } from "../../i18n";
import { useApp } from "../../store";
import { Alert, Button, Checkbox, inputClass, Section, Spinner } from "../ui";

const CATEGORIES: ObjectCategory[] = [
  "buildings",
  "grandstands",
  "scaffolding",
  "canopies",
  "barriers",
  "masts",
];
const SURFACES: SurfaceType[] = SURFACE_TYPES;
const EXCLUSIONS: ExclusionType[] = ["objects", "facades", "forests", "polygons", "roads"];
const MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"] as const;

function defaultPackageName(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `XPSB ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function toggle<T>(list: T[], value: T, on: boolean): T[] {
  return on ? [...new Set([...list, value])] : list.filter((v) => v !== value);
}

export function BriefStep({ onSubmitted }: { onSubmitted: () => void }) {
  const { t } = useT();
  const { job, settings, system, setJob, setEditingBrief } = useApp();
  const [brief, setBrief] = useState<Brief>(
    () =>
      job?.brief ?? {
        object_categories: ["buildings", "grandstands"],
        surface_types: [],
        surface_mode: "material",
        exclusions: [],
        use_ai: Boolean(system?.has_api_key && settings?.ai?.enabled),
        ai_model: settings?.ai?.model ?? "claude-opus-5-5",
        ai_cost_limit_usd: settings?.ai?.job_limit_usd ?? 5,
        ai_detect: true,
        detail: "medium",
        package_name: defaultPackageName(),
        notes: "",
      },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const update = (patch: Partial<Brief>) => setBrief((b) => ({ ...b, ...patch }));
  const nothingSelected = !brief.object_categories?.length && !brief.surface_types?.length;

  const submit = async () => {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await api.putBrief(job.id, brief));
      setEditingBrief(false);
      onSubmitted();
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <p className="text-sm text-zinc-600 dark:text-zinc-300">{t("brief.intro")}</p>

      <Section title={t("brief.objects")}>
        <div className="grid grid-cols-1 gap-1.5">
          {CATEGORIES.map((c) => (
            <Checkbox
              key={c}
              label={t(`category.${c}`)}
              checked={brief.object_categories?.includes(c) ?? false}
              onChange={(on) => update({ object_categories: toggle(brief.object_categories ?? [], c, on) })}
            />
          ))}
        </div>
      </Section>

      <Section title={t("brief.surfaces")}>
        <div className="grid grid-cols-2 gap-1.5">
          {SURFACES.map((s) => (
            <Checkbox
              key={s}
              label={t(`surface.${s}`)}
              checked={brief.surface_types?.includes(s) ?? false}
              onChange={(on) => update({ surface_types: toggle(brief.surface_types ?? [], s, on) })}
            />
          ))}
        </div>
        <label className="block text-sm">
          <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("brief.surface_mode")}</span>
          <select
            className={inputClass}
            value={brief.surface_mode}
            onChange={(e) => update({ surface_mode: e.target.value as Brief["surface_mode"] })}
          >
            <option value="material">{t("brief.surface_mode.material")}</option>
            <option value="imagery">{t("brief.surface_mode.imagery")}</option>
          </select>
        </label>
      </Section>

      <Section title={t("brief.exclusions")}>
        <p className="text-xs text-zinc-500">{t("brief.exclusions_hint")}</p>
        <div className="grid grid-cols-2 gap-1.5">
          {EXCLUSIONS.map((x) => (
            <Checkbox
              key={x}
              label={t(`exclusion.${x}`)}
              checked={brief.exclusions?.includes(x) ?? false}
              onChange={(on) => update({ exclusions: toggle(brief.exclusions ?? [], x, on) })}
            />
          ))}
        </div>
      </Section>

      <Section title={t("brief.ai")}>
        <Checkbox label={t("brief.use_ai")} checked={brief.use_ai ?? false} onChange={(on) => update({ use_ai: on })} />
        {brief.use_ai && (
          <div className="grid grid-cols-2 gap-2">
            <label className="text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("brief.ai_model")}</span>
              <select
                className={inputClass}
                value={brief.ai_model}
                onChange={(e) => update({ ai_model: e.target.value as Brief["ai_model"] })}
              >
                {MODELS.map((m) => (
                  <option key={m} value={m}>
                    {t(`model.${m}`)}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("brief.ai_limit")}</span>
              <input
                type="number"
                min={0}
                step={0.5}
                className={inputClass}
                value={brief.ai_cost_limit_usd}
                onChange={(e) => update({ ai_cost_limit_usd: Number(e.target.value) })}
              />
            </label>
            <div className="col-span-2 space-y-0.5">
              <Checkbox
                label={t("brief.ai_detect")}
                checked={brief.ai_detect ?? true}
                onChange={(on) => update({ ai_detect: on })}
              />
              <p className="pl-6 text-xs text-zinc-500">{t("brief.ai_detect_hint")}</p>
            </div>
          </div>
        )}
      </Section>

      <Section title={t("brief.detail")}>
        <select
          className={inputClass}
          value={brief.detail}
          onChange={(e) => update({ detail: e.target.value as Brief["detail"] })}
        >
          {(["low", "medium", "high"] as const).map((d) => (
            <option key={d} value={d}>
              {t(`detail.${d}`)}
            </option>
          ))}
        </select>
        <p className="text-xs text-zinc-500">{t("brief.detail_hint")}</p>
      </Section>

      <Section title={t("brief.package_name")}>
        <input
          className={inputClass}
          value={brief.package_name}
          maxLength={60}
          onChange={(e) => update({ package_name: e.target.value })}
          data-testid="package-name"
        />
      </Section>

      <Section title={t("brief.notes")}>
        <textarea
          className={`${inputClass} min-h-20`}
          maxLength={2000}
          placeholder={t("brief.notes_placeholder")}
          value={brief.notes}
          onChange={(e) => update({ notes: e.target.value })}
        />
      </Section>

      {error && <Alert kind="error">{error}</Alert>}
      <div className="flex justify-end gap-2">
        {job?.brief && (
          <Button variant="ghost" onClick={() => setEditingBrief(false)}>
            {t("common.cancel")}
          </Button>
        )}
        <Button variant="primary" onClick={submit} disabled={busy || nothingSelected} data-testid="brief-submit">
          {busy && <Spinner />}
          {t("brief.submit")}
        </Button>
      </div>
    </div>
  );
}
