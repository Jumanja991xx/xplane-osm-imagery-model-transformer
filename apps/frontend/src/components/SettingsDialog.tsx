import { useState } from "react";
import { api, ApiError, type Settings, type XPlaneCheck } from "../api/client";
import { useT } from "../i18n";
import { useApp } from "../store";
import { Alert, Button, inputClass, Section, Spinner } from "./ui";

const MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"] as const;

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
  const [apiKey, setApiKey] = useState("");
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
  const removeKey = () =>
    run(async () => {
      await api.deleteApiKey();
      setSystem(await api.system());
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <h2 className="text-base font-semibold">{t("settings.title")}</h2>
          <Button variant="ghost" onClick={() => setSettingsOpen(false)}>
            {t("common.close")}
          </Button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
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

          <Section title={t("settings.api_key")}>
            {!system?.keyring_available ? (
              <Alert kind="warning">{t("settings.keyring_unavailable")}</Alert>
            ) : (
              <>
                <p className="text-sm">
                  {system.has_api_key ? t("settings.api_key_set") : t("settings.api_key_missing")}
                </p>
                <div className="flex gap-2">
                  <input
                    type="password"
                    autoComplete="off"
                    className={inputClass}
                    placeholder={t("settings.api_key_placeholder")}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                  />
                  <Button onClick={saveKey} disabled={busy || !apiKey.trim()}>
                    {t("common.save")}
                  </Button>
                  {system.has_api_key && (
                    <Button variant="ghost" onClick={removeKey} disabled={busy}>
                      {t("common.remove")}
                    </Button>
                  )}
                </div>
                <p className="text-xs text-zinc-500">{t("settings.api_key_hint")}</p>
              </>
            )}
          </Section>

          <Section title={t("settings.ai_defaults")}>
            <div className="grid grid-cols-2 gap-2">
              <select
                className={inputClass}
                value={draft.ai?.model}
                onChange={(e) =>
                  setDraft({ ...draft, ai: { ...draft.ai!, model: e.target.value as (typeof MODELS)[number] } })
                }
              >
                {MODELS.map((m) => (
                  <option key={m} value={m}>
                    {t(`model.${m}`)}
                  </option>
                ))}
              </select>
              <input
                type="number"
                min={0}
                step={0.5}
                className={inputClass}
                title={t("brief.ai_limit")}
                value={draft.ai?.job_limit_usd}
                onChange={(e) => setDraft({ ...draft, ai: { ...draft.ai!, job_limit_usd: Number(e.target.value) } })}
              />
            </div>
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

          {message && <Alert kind={message.kind}>{message.text}</Alert>}
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
