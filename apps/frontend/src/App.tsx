import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import { MapView } from "./components/MapView";
import { SettingsDialog } from "./components/SettingsDialog";
import { Alert, Button, Spinner } from "./components/ui";
import { Wizard } from "./components/Wizard";
import { I18nProvider, useT, type Language } from "./i18n";
import { useApp } from "./store";

function Shell() {
  const { t } = useT();
  const { system, settingsOpen, setSettingsOpen } = useApp();
  const xplaneMissing = system && !system.xplane?.valid;

  return (
    <div className="flex h-full">
      <aside className="flex w-[420px] shrink-0 flex-col border-r border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900">
        <header className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <h1 className="text-sm font-semibold">{t("app.title")}</h1>
          <Button variant="ghost" onClick={() => setSettingsOpen(true)} data-testid="open-settings">
            ⚙ {t("app.settings")}
          </Button>
        </header>
        <div className="space-y-2 px-4 pt-3">
          <Alert>{t("app.milestone_notice")}</Alert>
          {xplaneMissing && (
            <Alert kind="warning">
              {t("settings.xplane_missing_banner")}{" "}
              <button className="underline" onClick={() => setSettingsOpen(true)}>
                {t("app.settings")}
              </button>
            </Alert>
          )}
        </div>
        <div className="min-h-0 flex-1">
          <Wizard />
        </div>
      </aside>
      <main className="relative min-w-0 flex-1">
        <MapView />
      </main>
      {settingsOpen && <SettingsDialog />}
    </div>
  );
}

export default function App() {
  const { settings, setSettings, setSystem } = useApp();
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setError(false);
    // The sidecar may still be starting: retry for a while before giving up.
    for (let attempt = 0; attempt < 40; attempt++) {
      try {
        const [s, sys] = await Promise.all([api.getSettings(), api.system()]);
        setSettings(s);
        setSystem(sys);
        return;
      } catch {
        await new Promise((r) => setTimeout(r, 500));
      }
    }
    setError(true);
  }, [setSettings, setSystem]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    // Development/browser tests: open an existing job without clicking through the wizard.
    (window as unknown as { __xpsbOpenJob?: (id: string) => Promise<void> }).__xpsbOpenJob = async (id) =>
      useApp.getState().setJob(await api.getJob(id));
  }, []);

  const lang: Language = settings?.language ?? (navigator.language.startsWith("de") ? "de" : "en");

  return (
    <I18nProvider lang={lang}>
      {settings ? <Shell /> : <Startup error={error} onRetry={load} />}
    </I18nProvider>
  );
}

function Startup({ error, onRetry }: { error: boolean; onRetry: () => void }) {
  const { t } = useT();
  return (
    <div className="flex h-full items-center justify-center">
      {error ? (
        <div className="space-y-3 text-center">
          <Alert kind="error">{t("app.backend_failed")}</Alert>
          <Button onClick={onRetry}>{t("app.retry")}</Button>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-sm">
          <Spinner /> {t("app.backend_starting")}
        </div>
      )}
    </div>
  );
}
