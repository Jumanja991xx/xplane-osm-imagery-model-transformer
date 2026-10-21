import { useEffect, useState } from "react";
import { SURFACE_TYPES } from "../../scene";
import {
  api,
  ApiError,
  type Brief,
  type ExclusionType,
  type ObjectCategory,
  type OtherScenery,
  type SurfaceType,
} from "../../api/client";
import { useT } from "../../i18n";
import { folderName, validPackageName } from "../../names";
import { useApp } from "../../store";
import { Alert, Button, Checkbox, inputClass, Section, Spinner } from "../ui";

const CATEGORIES: ObjectCategory[] = [
  "buildings",
  "grandstands",
  "scaffolding",
  "canopies",
  "barriers",
  "masts",
  "monuments",
  "bridges",
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
        // Most users do not know in advance what is there: find everything.
        object_categories: [...CATEGORIES],
        surface_types: [...SURFACES],
        surface_mode: "material",
        // Houses of X-Plane/simHeaven would otherwise stand inside the new objects.
        exclusions: ["facades", "forests", "objects"],
        exclusion_scope: "objects",
        use_ai: Boolean(system?.has_api_key && settings?.ai?.enabled),
        ai_model: settings?.ai?.model ?? "claude-opus-5-5",
        ai_cost_limit_usd: settings?.ai?.job_limit_usd ?? 5,
        ai_detect: true,
        ai_economy: true,
        mode: "objects",
        city_preset: "central_europe",
        research: true,
        ai_design: "important",
        detail: "medium",
        package_name: defaultPackageName(),
        notes: "",
      },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Sceneries already installed for this tile (orthos, other objects).
  const [others, setOthers] = useState<OtherScenery[]>([]);
  useEffect(() => {
    if (job) void api.otherSceneries(job.id).then(setOthers).catch(() => setOthers([]));
  }, [job]);
  const ortho = others.some((o) => o.enabled && (o.kind === "ortho" || o.kind === "mesh"));
  const everything =
    (brief.object_categories?.length ?? 0) === CATEGORIES.length &&
    (brief.surface_types?.length ?? 0) === SURFACES.length;
  const [restrict, setRestrict] = useState(!everything);
  const update = (patch: Partial<Brief>) => setBrief((b) => ({ ...b, ...patch }));
  const nothingSelected = !brief.object_categories?.length && !brief.surface_types?.length;
  const nameOk = validPackageName(brief.package_name ?? "");
  const nameSuggestion = folderName(brief.package_name ?? "");

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

      <Section title={t("brief.what")}>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            className="mt-1"
            checked={!restrict}
            onChange={() => {
              setRestrict(false);
              update({ object_categories: [...CATEGORIES], surface_types: [...SURFACES] });
            }}
            data-testid="find-everything"
          />
          <span>
            <span className="font-medium">{t("brief.everything")}</span>
            <span className="block text-xs text-zinc-500">{t("brief.everything_hint")}</span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="radio" className="mt-1" checked={restrict} onChange={() => setRestrict(true)} data-testid="restrict" />
          <span>
            <span className="font-medium">{t("brief.restrict")}</span>
            <span className="block text-xs text-zinc-500">{t("brief.restrict_hint")}</span>
          </span>
        </label>
        {restrict && (
          <div className="space-y-3 rounded-md bg-white p-3 ring-1 ring-zinc-200 dark:bg-zinc-800 dark:ring-zinc-700">
            <div>
              <div className="mb-1 text-xs font-semibold text-zinc-500 uppercase">{t("brief.objects")}</div>
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
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold text-zinc-500 uppercase">{t("brief.surfaces")}</div>
              <div className="grid grid-cols-2 gap-1.5">
                {SURFACES.map((x) => (
                  <Checkbox
                    key={x}
                    label={t(`surface.${x}`)}
                    checked={brief.surface_types?.includes(x) ?? false}
                    onChange={(on) => update({ surface_types: toggle(brief.surface_types ?? [], x, on) })}
                  />
                ))}
              </div>
            </div>
          </div>
        )}
      </Section>

      {others.length > 0 && (
        <Alert kind={ortho ? "warning" : "info"}>
          <div data-testid="other-sceneries">
            <div className="font-medium">{t("brief.others_title")}</div>
            <ul className="my-1 list-disc pl-5 text-xs">
              {others.map((o) => (
                <li key={o.name}>
                  {o.name} – {t(`brief.others_kind.${o.kind}`)}
                  {!o.enabled && ` (${t("brief.others_disabled")})`}
                </li>
              ))}
            </ul>
            <div className="text-xs">{t("brief.others_objects_hint")}</div>
            {ortho && brief.surface_mode !== "none" && (
              <div className="mt-1 flex items-center gap-2 text-xs">
                <span>{t("brief.others_ortho_hint")}</span>
                <Button onClick={() => update({ surface_mode: "none" })} data-testid="surface-none-suggest">
                  {t("brief.others_apply")}
                </Button>
              </div>
            )}
          </div>
        </Alert>
      )}
      <Section title={t("brief.mode")}>
        {(["objects", "city"] as const).map((mode) => (
          <label key={mode} className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="build_mode"
              className="mt-1"
              checked={(brief.mode ?? "objects") === mode}
              onChange={() => update({ mode })}
              data-testid={`build-mode-${mode}`}
            />
            <span>
              {t(`brief.mode.${mode}`)}
              <span className="block text-xs text-zinc-500">{t(`brief.mode.${mode}_hint`)}</span>
            </span>
          </label>
        ))}
        {brief.mode === "city" && (
          <label className="block text-sm">
            <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("brief.city_preset")}</span>
            <select
              className={inputClass}
              value={brief.city_preset ?? "central_europe"}
              onChange={(e) => update({ city_preset: e.target.value as Brief["city_preset"] })}
              data-testid="city-preset"
            >
              {(["central_europe", "central_europe_city"] as const).map((p) => (
                <option key={p} value={p}>
                  {t(`brief.city_preset.${p}`)}
                </option>
              ))}
            </select>
          </label>
        )}
      </Section>

      <Section title={t("brief.surface_mode")}>
        {(["material", "imagery", "none"] as const).map((mode) => (
          <label key={mode} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="surface_mode"
              checked={(brief.surface_mode ?? "material") === mode}
              onChange={() => update({ surface_mode: mode })}
              data-testid={`surface-mode-${mode}`}
            />
            {t(`brief.surface_mode.${mode}`)}
          </label>
        ))}
        {brief.surface_mode === "imagery" && <p className="text-xs text-zinc-500">{t("brief.surface_mode_hint")}</p>}
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
        {(brief.exclusions?.length ?? 0) > 0 && (
          <div className="space-y-1">
            <div className="text-xs font-semibold text-zinc-500 uppercase">{t("brief.exclusion_scope")}</div>
            {(["objects", "area"] as const).map((scope) => (
              <label key={scope} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="exclusion_scope"
                  checked={(brief.exclusion_scope ?? "objects") === scope}
                  onChange={() => update({ exclusion_scope: scope })}
                />
                {t(`brief.exclusion_scope.${scope}`)}
              </label>
            ))}
          </div>
        )}
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
              <Checkbox
                label={t("brief.ai_economy")}
                checked={brief.ai_economy ?? true}
                onChange={(on) => update({ ai_economy: on })}
              />
              <p className="pl-6 text-xs text-zinc-500">{t("brief.ai_economy_hint")}</p>
            </div>
            <div className="col-span-2 space-y-1">
              <div className="text-xs font-semibold text-zinc-500 uppercase">{t("brief.ai_design")}</div>
              {(["important", "all", "off"] as const).map((mode) => (
                <label key={mode} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="ai_design"
                    checked={(brief.ai_design ?? "important") === mode}
                    onChange={() => update({ ai_design: mode })}
                    data-testid={`ai-design-${mode}`}
                  />
                  {t(`brief.ai_design.${mode}`)}
                </label>
              ))}
              <p className="text-xs text-zinc-500">{t("brief.ai_design_hint")}</p>
            </div>
          </div>
        )}
      </Section>

      <Section title={t("brief.research")}>
        <Checkbox
          label={t("brief.research_on")}
          checked={brief.research ?? true}
          onChange={(on) => update({ research: on })}
        />
        <p className="pl-6 text-xs text-zinc-500">{t("brief.research_hint")}</p>
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
        {!nameOk && (
          <div className="space-y-1 text-xs text-amber-600" data-testid="package-name-invalid">
            <p>{t("brief.package_name_invalid")}</p>
            {validPackageName(nameSuggestion) && (
              <Button variant="ghost" onClick={() => update({ package_name: nameSuggestion })} data-testid="package-name-fix">
                {t("brief.package_name_use", { name: nameSuggestion })}
              </Button>
            )}
          </div>
        )}
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
