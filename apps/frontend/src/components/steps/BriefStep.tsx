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
import { hasKeyFor, modelGroups, modelLabel, providerOf, useAiModels } from "../../ai";
import { useT } from "../../i18n";
import { folderName, validPackageName } from "../../names";
import { useApp } from "../../store";
import { Alert, Button, Checkbox, inputClass, Section, Spinner, StepBar } from "../ui";

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

function defaultPackageName(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `XPSB ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function toggle<T>(list: T[], value: T, on: boolean): T[] {
  return on ? [...new Set([...list, value])] : list.filter((v) => v !== value);
}

export function BriefStep({
  onSubmitted,
  back,
}: {
  onSubmitted: () => void;
  back: { label: string; onClick: () => void; testId?: string };
}) {
  const { t } = useT();
  const { job, settings, system, setJob, setEditingBrief, setSettingsOpen } = useApp();
  const [models] = useAiModels();
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
        use_ai: Boolean(hasKeyFor(settings?.ai?.model, system) && settings?.ai?.enabled),
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

  const keyOk = hasKeyFor(brief.ai_model, system);
  const radio = (name: string, checked: boolean, onChange: () => void, label: string, hint?: string, testId?: string) => (
    <label key={`${name}-${label}`} className="flex items-start gap-2 text-sm">
      <input type="radio" name={name} className="mt-1" checked={checked} onChange={onChange} data-testid={testId} />
      <span>
        {label}
        {hint && <span className="block text-xs text-zinc-500">{hint}</span>}
      </span>
    </label>
  );

  return (
    <div className="space-y-5">
      <p className="text-sm text-zinc-600 dark:text-zinc-300">{t("brief.intro")}</p>

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

      <Section title={t("brief.what")}>
        {radio(
          "what",
          !restrict,
          () => {
            setRestrict(false);
            update({ object_categories: [...CATEGORIES], surface_types: [...SURFACES] });
          },
          t("brief.everything"),
          t("brief.everything_hint"),
          "find-everything",
        )}
        {radio("what", restrict, () => setRestrict(true), t("brief.restrict"), t("brief.restrict_hint"), "restrict")}
        {restrict && (
          <div className="space-y-3 rounded-md bg-white p-3 ring-1 ring-zinc-200 dark:bg-zinc-800 dark:ring-zinc-700">
            <div>
              <div className="mb-1 text-xs font-semibold text-zinc-500 uppercase">{t("brief.objects")}</div>
              <div className="grid grid-cols-2 gap-1.5">
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

      <Section title={t("brief.mode")}>
        {(["objects", "city"] as const).map((mode) =>
          radio(
            "build_mode",
            (brief.mode ?? "objects") === mode,
            () => update({ mode }),
            t(`brief.mode.${mode}`),
            t(`brief.mode.${mode}_hint`),
            `build-mode-${mode}`,
          ),
        )}
        {brief.mode === "city" && (
          <select
            className={inputClass}
            value={brief.city_preset ?? "central_europe"}
            onChange={(e) => update({ city_preset: e.target.value as Brief["city_preset"] })}
            data-testid="city-preset"
            aria-label={t("brief.city_preset")}
          >
            {(["central_europe", "central_europe_city"] as const).map((p) => (
              <option key={p} value={p}>
                {t(`brief.city_preset.${p}`)}
              </option>
            ))}
          </select>
        )}
      </Section>

      <Section title={t("brief.surface_mode")}>
        {(["material", "imagery", "none"] as const).map((mode) =>
          radio(
            "surface_mode",
            (brief.surface_mode ?? "material") === mode,
            () => update({ surface_mode: mode }),
            t(`brief.surface_mode.${mode}`),
            undefined,
            `surface-mode-${mode}`,
          ),
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

      <Section title={t("brief.ai")}>
        <Checkbox label={t("brief.use_ai")} checked={brief.use_ai ?? false} onChange={(on) => update({ use_ai: on })} />
        {brief.use_ai && (
          <div className="grid grid-cols-2 gap-2">
            <label className="col-span-2 text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("brief.ai_model")}</span>
              <select
                className={inputClass}
                value={brief.ai_model}
                onChange={(e) => update({ ai_model: e.target.value })}
                data-testid="brief-model"
              >
                {modelGroups(models, brief.ai_model).map((g) => (
                  <optgroup key={g.provider} label={t(`provider.${g.provider}`)}>
                    {g.models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {modelLabel(t, m)}
                        {!hasKeyFor(m.id, system) ? ` (${t("brief.no_key")})` : ""}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <label className="col-span-2 text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("brief.ai_limit")}</span>
              <span className="flex items-start gap-3">
                <span className="block w-28 shrink-0">
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    className={inputClass}
                    value={brief.ai_cost_limit_usd}
                    onChange={(e) => update({ ai_cost_limit_usd: Number(e.target.value) })}
                    data-testid="brief-limit"
                  />
                </span>
                <span className="min-w-0 flex-1 text-xs text-zinc-500">
                  {t("brief.ai_limit_month", {
                    left: Math.max(0, (settings?.ai?.monthly_limit_usd ?? 0) - (system?.ai_month_spent ?? 0)).toFixed(2),
                    monthly: settings?.ai?.monthly_limit_usd ?? 0,
                  })}
                </span>
              </span>
            </label>
            {!keyOk && (
              <div className="col-span-2">
                <Alert kind="warning">
                  {t("brief.key_missing", { provider: t(`provider.${providerOf(brief.ai_model ?? "")}`) })}{" "}
                  <button className="underline" onClick={() => setSettingsOpen(true)}>
                    {t("app.settings")}
                  </button>
                </Alert>
              </div>
            )}
          </div>
        )}
      </Section>

      <Section title={t("brief.notes")}>
        <textarea
          className={`${inputClass} min-h-16`}
          maxLength={2000}
          placeholder={t("brief.notes_placeholder")}
          value={brief.notes}
          onChange={(e) => update({ notes: e.target.value })}
        />
      </Section>

      <details className="group rounded-md ring-1 ring-zinc-200 dark:ring-zinc-700" data-testid="brief-more">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium select-none">{t("brief.more")}</summary>
        <div className="space-y-5 border-t border-zinc-200 px-3 py-3 dark:border-zinc-700">
          {brief.use_ai && (
            <Section title={t("brief.ai_more")}>
              <Checkbox label={t("brief.ai_detect")} checked={brief.ai_detect ?? true} onChange={(on) => update({ ai_detect: on })} />
              <p className="pl-6 text-xs text-zinc-500">{t("brief.ai_detect_hint")}</p>
              <Checkbox label={t("brief.ai_economy")} checked={brief.ai_economy ?? true} onChange={(on) => update({ ai_economy: on })} />
              <p className="pl-6 text-xs text-zinc-500">{t("brief.ai_economy_hint")}</p>
              <div className="pt-1 text-sm font-medium">{t("brief.ai_design")}</div>
              {(["important", "all", "off"] as const).map((mode) =>
                radio(
                  "ai_design",
                  (brief.ai_design ?? "important") === mode,
                  () => update({ ai_design: mode }),
                  t(`brief.ai_design.${mode}`),
                  undefined,
                  `ai-design-${mode}`,
                ),
              )}
            </Section>
          )}
          <Section title={t("brief.research")}>
            <Checkbox label={t("brief.research_on")} checked={brief.research ?? true} onChange={(on) => update({ research: on })} />
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
            {(brief.exclusions?.length ?? 0) > 0 &&
              (["objects", "area"] as const).map((scope) =>
                radio(
                  "exclusion_scope",
                  (brief.exclusion_scope ?? "objects") === scope,
                  () => update({ exclusion_scope: scope }),
                  t(`brief.exclusion_scope.${scope}`),
                ),
              )}
          </Section>
        </div>
      </details>

      {error && <Alert kind="error">{error}</Alert>}
      <StepBar back={back}>
        <Button variant="primary" onClick={submit} disabled={busy || nothingSelected} data-testid="brief-submit">
          {busy && <Spinner />}
          {t("brief.submit")}
        </Button>
      </StepBar>
    </div>
  );
}
