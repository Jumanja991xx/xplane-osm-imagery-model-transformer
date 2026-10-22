import { useState } from "react";
import { api, ApiError, type BlenderTest, type ImagerySettings, type Settings, type XPlaneCheck } from "../api/client";
import { modelGroups, modelLabel, useAiModels } from "../ai";
import { useT } from "../i18n";
import { useApp } from "../store";
import { IMAGERY_PRESETS } from "../imagery/presets";
import { Alert, Button, Checkbox, inputClass, Section, Spinner } from "./ui";

const TABS = ["general", "ai", "imagery", "tools"] as const;
const IMAGERY_CHOICES: ImagerySettings["source"][] = [
  "auto",
  "de_states",
  "bayern_dop40",
  "nrw_dop",
  "swissimage",
  "basemap_at",
  "pdok_nl",
  "ign_fr",
  "pnoa_es",
  "usgs_naip",
  "gsi_jp",
  "esri",
  "custom",
  "none",
];

function CheckBadge({ check }: { check: XPlaneCheck }) {
  const { t } = useT();
  return check.valid ? (
    <span className="text-xs text-emerald-600">✓ {t("settings.valid")}</span>
  ) : (
    <span className="text-xs text-red-600">
      ✗ {t("settings.invalid")}: {(check.problems ?? []).map((p) => t(`problem.${p}`)).join(", ")}
    </span>
  );
}

export function SettingsDialog() {
  const { t } = useT();
  const { settings, system, setSettings, setSystem, setSettingsOpen } = useApp();
  const [draft, setDraft] = useState<Settings>(() => structuredClone(settings!));
  const [candidates, setCandidates] = useState<XPlaneCheck[] | null>(null);
  const [xplaneCheck, setXplaneCheck] = useState<XPlaneCheck | null>(system?.xplane ?? null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("general");
  const [apiKey, setApiKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [geminiLoaded, setGeminiLoaded] = useState<number | null>(null);
  const [models, addModels] = useAiModels();
  const [mapillary, setMapillary] = useState("");
  const [esriKey, setEsriKey] = useState("");
  const imagery: ImagerySettings = {
    source: "auto",
    custom_type: "wms",
    custom_max_zoom: 19,
    custom_url: "",
    custom_layer: "",
    custom_attribution: "",
    custom_rights_confirmed: false,
    custom_subdomains: "",
    custom_on_map: true,
    ...draft.imagery,
  };
  const setImagery = (change: Partial<ImagerySettings>) =>
    setDraft({ ...draft, imagery: { ...imagery, ...change } });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof ApiError ? t(e.key, e.params) : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const detect = () => run(async () => setCandidates(await api.detectXPlane()));
  // Runs on blur too, so it must not lock the buttons: otherwise a click on
  // "Save" right after typing would land on a disabled button and get lost.
  const checkXPlane = async () => {
    try {
      setXplaneCheck(draft.xplane_path ? await api.checkXPlane(draft.xplane_path) : null);
    } catch {
      setXplaneCheck(null);
    }
  };
  const checkDsfTool = () =>
    run(async () => {
      await api.checkDsfTool(draft.dsftool_path ?? "");
      setSystem(await api.system());
    });
  const checkBlender = () =>
    run(async () => {
      setSettings(await api.putSettings(draft));
      setSystem(await api.system());
    });
  const save = () =>
    run(async () => {
      setSettings(await api.putSettings(draft));
      setSystem(await api.system());
      setMessage({ kind: "success", text: t("settings.saved") });
    });
  const saveKey = () =>
    run(async () => {
      await api.setApiKey(apiKey);
      setApiKey("");
      setSystem(await api.system());
      setMessage({ kind: "success", text: t("settings.saved") });
    });
  const [presetId, setPresetId] = useState("");
  const [sourceTest, setSourceTest] = useState<{ ok: boolean; detail?: string | null } | null>(null);
  const loadPreset = (id: string) => {
    setPresetId(id);
    setSourceTest(null);
    const preset = IMAGERY_PRESETS.find((p) => p.id === id);
    // rights stay unconfirmed: the user reads the terms and ticks the box
    if (preset) setImagery({ ...preset.settings, custom_rights_confirmed: false });
  };
  const testSource = () =>
    run(async () => {
      const preset = IMAGERY_PRESETS.find((p) => p.id === presetId);
      setSourceTest(await api.testImagery(imagery, preset?.test[0], preset?.test[1]));
    });
  const [blenderTest, setBlenderTest] = useState<BlenderTest | "running" | null>(null);
  const testBlender = () =>
    run(async () => {
      setBlenderTest("running");
      try {
        setBlenderTest(
          await api.testBlender(draft.blender_path ?? null, draft.blender_device === "cpu" ? "cpu" : "auto"),
        );
      } catch (e) {
        setBlenderTest(null);
        throw e;
      }
    });
  const [esriCheck, setEsriCheck] = useState<{ ok?: boolean | null; detail?: string | null } | null>(null);
  const saveEsri = () =>
    run(async () => {
      setEsriCheck(null);
      const check = await api.setEsriKey(esriKey);
      setEsriKey("");
      setSystem(await api.system());
      setEsriCheck(check);
    });
  const testEsri = () =>
    run(async () => {
      setEsriCheck(null);
      setEsriCheck(await api.checkEsriKey());
    });
  const removeEsri = () =>
    run(async () => {
      await api.deleteEsriKey();
      setSystem(await api.system());
    });
  const saveMapillary = () =>
    run(async () => {
      await api.setMapillaryToken(mapillary.trim());
      setMapillary("");
      setSystem(await api.system());
      setMessage({ kind: "success", text: t("settings.saved") });
    });
  const removeMapillary = () =>
    run(async () => {
      await api.deleteMapillaryToken();
      setSystem(await api.system());
    });
  const saveGemini = () =>
    run(async () => {
      await api.setGeminiKey(geminiKey);
      setGeminiKey("");
      setSystem(await api.system());
      setMessage({ kind: "success", text: t("settings.saved") });
    });
  const removeGemini = () =>
    run(async () => {
      await api.deleteGeminiKey();
      setSystem(await api.system());
    });
  const loadGemini = () =>
    run(async () => {
      const found = await api.geminiModels();
      addModels(found);
      setGeminiLoaded(found.length);
    });
  const removeKey = () =>
    run(async () => {
      await api.deleteApiKey();
      setSystem(await api.system());
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="flex h-[85vh] w-full max-w-3xl flex-col rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <h2 className="text-base font-semibold">
            {t("settings.title")}{" "}
            <span className="text-xs font-normal text-zinc-500" data-testid="build-id">
              {system?.version} · Build {(import.meta.env.VITE_BUILD_ID ?? "dev").slice(0, 7)}
            </span>
          </h2>
          <Button variant="ghost" onClick={() => setSettingsOpen(false)}>
            {t("common.close")}
          </Button>
        </div>

        <div className="flex min-h-0 flex-1">
          <nav className="w-40 shrink-0 space-y-0.5 border-r border-zinc-200 p-2 dark:border-zinc-700" role="tablist">
            {TABS.map((id) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`block w-full rounded-md px-3 py-2 text-left text-sm ${
                  tab === id
                    ? "bg-accent-50 font-medium text-accent-700 dark:bg-accent-700/30 dark:text-accent-100"
                    : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
                }`}
                data-testid={`settings-tab-${id}`}
              >
                {t(`settings.tab.${id}`)}
              </button>
            ))}
          </nav>
          <div className="min-w-0 flex-1 space-y-6 overflow-y-auto px-5 py-4">
          {tab === "general" && (
            <>
          <Section title={t("settings.xplane")}>
            <div className="flex gap-2">
              <input
                className={inputClass}
                value={draft.xplane_path ?? ""}
                placeholder="C:\X-Plane 12"
                onChange={(e) => setDraft({ ...draft, xplane_path: e.target.value || null })}
                onBlur={checkXPlane}
                data-testid="xplane-path"
              />
              <Button onClick={checkXPlane} disabled={busy}>
                {t("common.check")}
              </Button>
            </div>
            {xplaneCheck && (
              <div>
                <CheckBadge check={xplaneCheck} />
              </div>
            )}
            <div>
              <Button onClick={detect} disabled={busy}>
                {t("settings.xplane_detect")}
              </Button>
            </div>
            {candidates && candidates.length === 0 && (
              <p className="text-xs text-zinc-500">{t("settings.xplane_none_found")}</p>
            )}
            {candidates?.map((c) => (
              <div key={c.path} className="flex items-center justify-between gap-2 rounded-md bg-zinc-50 px-2 py-1 dark:bg-zinc-800">
                <div className="min-w-0">
                  <div className="truncate text-sm">{c.path}</div>
                  <CheckBadge check={c} />
                </div>
                <Button
                  disabled={!c.valid}
                  onClick={() => {
                    setDraft({ ...draft, xplane_path: c.path });
                    setXplaneCheck(c);
                  }}
                >
                  {t("settings.xplane_use")}
                </Button>
              </div>
            ))}
          </Section>

          <Section title={t("app.language")}>
            <select
              className={inputClass}
              value={draft.language}
              onChange={(e) => setDraft({ ...draft, language: e.target.value as Settings["language"] })}
            >
              <option value="de">Deutsch</option>
              <option value="en">English</option>
            </select>
          </Section>

            </>
          )}
          {tab === "ai" && (
            <>
          {!system?.keyring_available && <Alert kind="warning">{t("settings.keyring_unavailable")}</Alert>}
          <p className="text-sm text-zinc-600 dark:text-zinc-300">{t("settings.ai_intro")}</p>
          {system?.keyring_available && (
            <>
              <KeyField
                title={t("provider.anthropic")}
                hint={t("settings.api_key_hint")}
                has={system.has_api_key}
                placeholder={t("settings.api_key_placeholder")}
                value={apiKey}
                onChange={setApiKey}
                onSave={saveKey}
                onRemove={removeKey}
                busy={busy}
                testId="api-key"
              />
              <KeyField
                title={t("provider.google")}
                hint={t("settings.gemini_key_hint")}
                has={Boolean(system.has_gemini_key)}
                placeholder={t("settings.gemini_key_placeholder")}
                value={geminiKey}
                onChange={setGeminiKey}
                onSave={saveGemini}
                onRemove={removeGemini}
                busy={busy}
                testId="gemini-key"
              />
            </>
          )}
          <Section title={t("settings.ai_defaults")}>
            <label className="block text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("brief.ai_model")}</span>
              <select
                className={inputClass}
                value={draft.ai?.model}
                onChange={(e) => setDraft({ ...draft, ai: { ...draft.ai!, model: e.target.value } })}
                data-testid="settings-model"
              >
                {modelGroups(models, draft.ai?.model).map((g) => (
                  <optgroup key={g.provider} label={t(`provider.${g.provider}`)}>
                    {g.models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {modelLabel(t, m)} · {t("settings.model_price", { input: m.price_in, output: m.price_out })}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            {system?.has_gemini_key && (
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="ghost" onClick={loadGemini} disabled={busy} data-testid="gemini-load">
                  {t("settings.gemini_load")}
                </Button>
                {geminiLoaded !== null && (
                  <span className="text-xs text-zinc-500">{t("settings.gemini_loaded", { count: geminiLoaded })}</span>
                )}
              </div>
            )}
            <LimitField
              label={t("settings.job_limit")}
              hint={t("settings.job_limit_hint")}
              value={draft.ai?.job_limit_usd ?? 0}
              step={0.5}
              onChange={(v) => setDraft({ ...draft, ai: { ...draft.ai!, job_limit_usd: v } })}
              testId="job-limit"
            />
            <LimitField
              label={t("settings.monthly_limit")}
              hint={t("settings.monthly_limit_hint", { spent: (system?.ai_month_spent ?? 0).toFixed(2) })}
              value={draft.ai?.monthly_limit_usd ?? 0}
              step={1}
              onChange={(v) => setDraft({ ...draft, ai: { ...draft.ai!, monthly_limit_usd: v } })}
              testId="monthly-limit"
            />
            {(draft.ai?.monthly_limit_usd ?? 0) < (draft.ai?.job_limit_usd ?? 0) && (
              <Alert kind="warning">
                {t("settings.limit_conflict", {
                  monthly: draft.ai?.monthly_limit_usd,
                  job: draft.ai?.job_limit_usd,
                  left: Math.max(0, (draft.ai?.monthly_limit_usd ?? 0) - (system?.ai_month_spent ?? 0)).toFixed(2),
                })}
              </Alert>
            )}
          </Section>
            </>
          )}
          {tab === "imagery" && (
            <>
          <Section title={t("settings.imagery")}>
            <select
              className={inputClass}
              value={imagery.source}
              onChange={(e) => setImagery({ source: e.target.value as ImagerySettings["source"] })}
              data-testid="imagery-source"
            >
              {IMAGERY_CHOICES.map((c) => (
                <option key={c} value={c}>
                  {t(`imagery.choice.${c}`)}
                </option>
              ))}
            </select>
            <p className="text-xs text-zinc-500">{t(`imagery.hint.${imagery.source}`)}</p>
            {(imagery.source === "esri" || imagery.source === "auto") && system?.keyring_available && (
              <div className="space-y-1.5 rounded-md border border-zinc-200 p-2 dark:border-zinc-700">
                <p className="text-sm">{system.has_esri_key ? t("settings.esri_set") : t("settings.esri_missing")}</p>
                <div className="flex gap-2">
                  <input
                    type="password"
                    autoComplete="off"
                    className={inputClass}
                    placeholder={t("settings.esri_placeholder")}
                    value={esriKey}
                    onChange={(e) => setEsriKey(e.target.value)}
                    data-testid="esri-key"
                  />
                  <Button onClick={saveEsri} disabled={busy || esriKey.trim().length < 10}>
                    {t("common.save")}
                  </Button>
                  {system.has_esri_key && (
                    <Button variant="ghost" onClick={testEsri} disabled={busy} data-testid="esri-test">
                      {t("settings.esri_test")}
                    </Button>
                  )}
                  {system.has_esri_key && (
                    <Button variant="ghost" onClick={removeEsri} disabled={busy}>
                      {t("common.remove")}
                    </Button>
                  )}
                </div>
                {esriCheck?.ok === true && (
                  <div data-testid="esri-ok">
                    <Alert kind="success">{t("settings.esri_ok")}</Alert>
                  </div>
                )}
                {esriCheck?.ok === false && (
                  <div data-testid="esri-failed">
                    <Alert kind="warning">{t("settings.esri_failed", { detail: esriCheck.detail ?? "" })}</Alert>
                  </div>
                )}
                <p className="text-xs text-zinc-500">{t("settings.esri_hint")}</p>
              </div>
            )}
            {(imagery.source === "custom" || imagery.source === "esri") && (
              <div className="flex items-center gap-2">
                <select
                  className={`${inputClass} w-auto`}
                  value={presetId}
                  onChange={(e) => loadPreset(e.target.value)}
                  data-testid="imagery-preset"
                >
                  <option value="">{t("settings.imagery_preset")}</option>
                  {IMAGERY_PRESETS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label} – {p.region}
                    </option>
                  ))}
                </select>
                {presetId && (
                  <span className="text-xs text-zinc-500">
                    {IMAGERY_PRESETS.find((p) => p.id === presetId)?.license}
                  </span>
                )}
              </div>
            )}
            {imagery.source === "custom" && (
              <div className="space-y-2">
                <div className="flex gap-2">
                  <select
                    className={`${inputClass} w-auto`}
                    value={imagery.custom_type}
                    onChange={(e) => setImagery({ custom_type: e.target.value as ImagerySettings["custom_type"] })}
                    data-testid="imagery-type"
                  >
                    <option value="wms">WMS</option>
                    <option value="xyz">{t("settings.imagery_xyz")}</option>
                  </select>
                  {imagery.custom_type === "xyz" && (
                    <label className="flex items-center gap-1 text-xs text-zinc-500">
                      {t("settings.imagery_max_zoom")}
                      <input
                        type="number"
                        min={10}
                        max={23}
                        className={`${inputClass} w-16`}
                        value={imagery.custom_max_zoom}
                        onChange={(e) => setImagery({ custom_max_zoom: Number(e.target.value) })}
                      />
                    </label>
                  )}
                </div>
                <input
                  className={inputClass}
                  placeholder={imagery.custom_type === "xyz" ? "https://…/{z}/{x}/{y}.jpg" : "https://…/wms"}
                  value={imagery.custom_url}
                  onChange={(e) => setImagery({ custom_url: e.target.value })}
                  data-testid="imagery-url"
                />
                {imagery.custom_type === "xyz" && (
                  <>
                    <input
                      className={inputClass}
                      placeholder={t("settings.imagery_subdomains")}
                      value={imagery.custom_subdomains}
                      onChange={(e) => setImagery({ custom_subdomains: e.target.value })}
                      data-testid="imagery-subdomains"
                    />
                    <p className="text-xs text-zinc-500">{t("settings.imagery_xyz_hint")}</p>
                    <div className="flex items-center gap-2">
                      <Button variant="ghost" onClick={testSource} disabled={busy} data-testid="imagery-test">
                        {t("settings.imagery_test")}
                      </Button>
                      {sourceTest?.ok === true && (
                        <span className="text-sm text-green-700" data-testid="imagery-test-ok">
                          {t("settings.imagery_test_ok")}
                        </span>
                      )}
                      {sourceTest?.ok === false && (
                        <span className="min-w-0 break-all text-sm text-amber-700" data-testid="imagery-test-failed">
                          {t("settings.imagery_test_failed", { detail: sourceTest.detail ?? "" })}
                        </span>
                      )}
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={imagery.custom_on_map}
                        onChange={(e) => setImagery({ custom_on_map: e.target.checked })}
                        data-testid="imagery-on-map"
                      />
                      {t("settings.imagery_on_map")}
                    </label>
                  </>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <input
                    className={inputClass}
                    disabled={imagery.custom_type === "xyz"}
                    placeholder={t("settings.imagery_layer")}
                    value={imagery.custom_layer}
                    onChange={(e) => setImagery({ custom_layer: e.target.value })}
                  />
                  <input
                    className={inputClass}
                    placeholder={t("settings.imagery_attribution")}
                    value={imagery.custom_attribution}
                    onChange={(e) => setImagery({ custom_attribution: e.target.value })}
                    data-testid="imagery-attribution"
                  />
                </div>
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={imagery.custom_rights_confirmed}
                    onChange={(e) => setImagery({ custom_rights_confirmed: e.target.checked })}
                    data-testid="imagery-rights"
                  />
                  {t("settings.imagery_rights")}
                </label>
              </div>
            )}
          </Section>

          {system?.keyring_available && (
            <Section title={t("settings.mapillary")}>
              <p className="text-sm">
                {system.has_mapillary_token ? t("settings.mapillary_set") : t("settings.mapillary_missing")}
              </p>
              <div className="flex gap-2">
                <input
                  type="password"
                  autoComplete="off"
                  className={inputClass}
                  placeholder="MLY|…"
                  value={mapillary}
                  onChange={(e) => setMapillary(e.target.value)}
                />
                <Button onClick={saveMapillary} disabled={busy || mapillary.trim().length < 10}>
                  {t("common.save")}
                </Button>
                {system.has_mapillary_token && (
                  <Button variant="ghost" onClick={removeMapillary} disabled={busy}>
                    {t("common.remove")}
                  </Button>
                )}
              </div>
              <p className="text-xs text-zinc-500">{t("settings.mapillary_hint")}</p>
            </Section>
          )}

            </>
          )}
          {tab === "tools" && (
            <>
          <Section title={t("settings.dsftool")}>
            <p className="text-sm">
              {system?.dsftool.found ? (
                <span className="text-emerald-600">{t("settings.dsftool_found", { version: system.dsftool.version })}</span>
              ) : (
                <span className="text-red-600">{t("settings.dsftool_missing")}</span>
              )}
            </p>
            {system?.dsftool.path && <code className="block text-xs break-all">{system.dsftool.path}</code>}
            <label className="block text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("settings.dsftool_custom")}</span>
              <div className="flex gap-2">
                <input
                  className={inputClass}
                  value={draft.dsftool_path ?? ""}
                  onChange={(e) => setDraft({ ...draft, dsftool_path: e.target.value || null })}
                />
                <Button onClick={checkDsfTool} disabled={busy}>
                  {t("common.check")}
                </Button>
              </div>
            </label>
          </Section>

          <Section title={t("settings.blender")}>
            <p className="text-xs text-zinc-600 dark:text-zinc-300">{t("settings.blender_intro")}</p>
            <p className="text-sm" data-testid="blender-status">
              {system?.blender ? (
                <span className="text-emerald-600">{t("settings.blender_found", { version: system.blender })}</span>
              ) : draft.use_blender === false ? (
                <span className="text-zinc-500">{t("settings.blender_off")}</span>
              ) : (
                <span className="text-zinc-500">{t("settings.blender_missing")}</span>
              )}
            </p>
            <Checkbox
              label={<span className="text-sm">{t("settings.blender_use")}</span>}
              checked={draft.use_blender !== false}
              onChange={(v) => setDraft({ ...draft, use_blender: v })}
            />
            <label className="block text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("settings.blender_custom")}</span>
              <div className="flex gap-2">
                <input
                  className={inputClass}
                  value={draft.blender_path ?? ""}
                  placeholder="C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
                  onChange={(e) => setDraft({ ...draft, blender_path: e.target.value || null })}
                />
                <Button onClick={checkBlender} disabled={busy}>
                  {t("common.check")}
                </Button>
              </div>
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("settings.blender_device")}</span>
              <select
                className={inputClass}
                value={draft.blender_device ?? "auto"}
                onChange={(e) => setDraft({ ...draft, blender_device: e.target.value as "auto" | "cpu" })}
              >
                <option value="auto">{t("settings.blender_device_auto")}</option>
                <option value="cpu">{t("settings.blender_device_cpu")}</option>
              </select>
            </label>
            <div className="space-y-1">
              <Button onClick={testBlender} disabled={busy} data-testid="blender-test">
                {t("settings.blender_test")}
              </Button>
              <BlenderTestResult result={blenderTest} />
            </div>
          </Section>

          <Section title={t("settings.osm")}>
            <p className="text-xs text-zinc-600 dark:text-zinc-300">{t("settings.osm_intro")}</p>
            <label className="block text-sm">
              <span className="mb-1 block text-zinc-600 dark:text-zinc-300">{t("settings.overpass_url")}</span>
              <input
                className={inputClass}
                placeholder="https://…/api/interpreter"
                value={draft.overpass_url ?? ""}
                onChange={(e) => setDraft({ ...draft, overpass_url: e.target.value.trim() || null })}
              />
            </label>
          </Section>

            </>
          )}
          {message && <Alert kind={message.kind}>{message.text}</Alert>}
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <Button variant="primary" onClick={save} disabled={busy} data-testid="settings-save">
            {busy && <Spinner />}
            {t("common.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function BlenderTestResult({ result }: { result: BlenderTest | "running" | null }) {
  const { t } = useT();
  if (!result) return null;
  if (result === "running") return <p className="text-xs text-zinc-500">{t("settings.blender_test_running")}</p>;
  const sec = (v?: number | null) => (v == null ? "–" : v.toFixed(1));
  return (
    <div className="text-xs" data-testid="blender-test-result">
      {result.ok ? (
        <p className="text-emerald-600">
          {t("settings.blender_test_ok", { bake: sec(result.bake_s), total: sec(result.total_s) })}
        </p>
      ) : (
        <p className="text-red-600">{t("settings.blender_test_failed", { problem: result.problem ?? "" })}</p>
      )}
      <p className="text-zinc-600 dark:text-zinc-300">
        {t("settings.blender_test_steps", {
          start: sec(result.start_s),
          search: sec(result.gpu_search_s),
          device: result.device || "–",
        })}
      </p>
      {result.gpu_search_hung && <p className="text-amber-600">{t("settings.blender_test_hung")}</p>}
      {result.log && <p className="break-all text-zinc-500">{t("settings.blender_test_log", { path: result.log })}</p>}
    </div>
  );
}

function KeyField({
  title,
  hint,
  has,
  placeholder,
  value,
  onChange,
  onSave,
  onRemove,
  busy,
  testId,
}: {
  title: string;
  hint: string;
  has: boolean;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  onSave: () => void;
  onRemove: () => void;
  busy: boolean;
  testId: string;
}) {
  const { t } = useT();
  return (
    <div className="space-y-1.5 rounded-md p-3 ring-1 ring-zinc-200 dark:ring-zinc-700">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{title}</span>
        <span className={`text-xs ${has ? "text-emerald-600" : "text-zinc-500"}`} data-testid={`${testId}-status`}>
          {has ? `✓ ${t("settings.key_set")}` : t("settings.key_missing")}
        </span>
      </div>
      <div className="flex gap-2">
        <input
          type="password"
          autoComplete="off"
          className={inputClass}
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          data-testid={testId}
        />
        <Button onClick={onSave} disabled={busy || !value.trim()} data-testid={`${testId}-save`}>
          {t("common.save")}
        </Button>
        {has && (
          <Button variant="ghost" onClick={onRemove} disabled={busy}>
            {t("common.remove")}
          </Button>
        )}
      </div>
      <p className="text-xs text-zinc-500">{hint}</p>
    </div>
  );
}

function LimitField({
  label,
  hint,
  value,
  step,
  onChange,
  testId,
}: {
  label: string;
  hint: string;
  value: number;
  step: number;
  onChange: (v: number) => void;
  testId: string;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-zinc-700 dark:text-zinc-200">{label}</span>
      <span className="flex items-start gap-3">
        <span className="block w-28 shrink-0">
          <input
            type="number"
            min={0}
            step={step}
            className={inputClass}
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            data-testid={testId}
          />
        </span>
        <span className="min-w-0 flex-1 text-xs text-zinc-500">{hint}</span>
      </span>
    </label>
  );
}
