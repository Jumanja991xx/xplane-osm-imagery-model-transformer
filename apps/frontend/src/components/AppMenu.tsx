import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useApp } from "../store";
import { Button } from "./ui";

const FINAL = ["installed", "cancelled"];

/** The app's header: the current job's name and one menu for everything that is not
 * a step of the job (new job, my jobs, settings, about). */
export function AppHeader() {
  const { t } = useT();
  const { job, system, reset, setJob, setSettingsOpen, setJobsOpen } = useApp();
  const [open, setOpen] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [about, setAbout] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const item = (label: string, action: () => void, testId: string, danger = false) => (
    <button
      type="button"
      role="menuitem"
      className={`block w-full px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
        danger ? "text-red-600" : ""
      }`}
      onClick={() => {
        setOpen(false);
        action();
      }}
      data-testid={testId}
    >
      {label}
    </button>
  );
  const name = job?.brief?.package_name;

  return (
    <header className="relative flex items-center justify-between gap-2 border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-800">
      <div className="min-w-0">
        <h1 className="text-sm font-semibold">{t("app.title")}</h1>
        <p className="truncate text-xs text-zinc-500" data-testid="job-title">
          {job ? (name ?? t("menu.unnamed_job")) : t("menu.no_job")}
        </p>
      </div>
      <div ref={ref} className="relative">
        <Button variant="ghost" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} data-testid="menu">
          ☰ {t("menu.title")}
        </Button>
        {open && (
          <div
            role="menu"
            className="absolute right-0 z-40 mt-1 w-56 overflow-hidden rounded-md bg-white py-1 shadow-lg ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-700"
          >
            {item(t("menu.new_job"), reset, "menu-new-job")}
            {item(t("menu.my_jobs"), () => setJobsOpen(true), "menu-jobs")}
            {job &&
              !FINAL.includes(job.state) &&
              item(t("menu.cancel_job"), () => setConfirmCancel(true), "cancel-job", true)}
            <div className="my-1 border-t border-zinc-200 dark:border-zinc-700" />
            {item(t("app.settings"), () => setSettingsOpen(true), "open-settings")}
            {item(t("menu.about"), () => setAbout(true), "menu-about")}
          </div>
        )}
      </div>
      {confirmCancel && job && (
        <div className="absolute inset-x-0 top-full z-30 flex items-center justify-between gap-2 border-b border-red-200 bg-red-50 px-4 py-2 text-sm dark:border-red-900 dark:bg-red-950">
          <span>{t("common.cancel_confirm")}</span>
          <span className="flex gap-2">
            <Button
              variant="danger"
              onClick={async () => {
                setConfirmCancel(false);
                setJob(await api.cancel(job.id));
              }}
              data-testid="cancel-confirm"
            >
              {t("common.yes")}
            </Button>
            <Button onClick={() => setConfirmCancel(false)}>{t("common.no")}</Button>
          </span>
        </div>
      )}
      {about && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-sm space-y-3 rounded-lg bg-white p-5 text-sm shadow-xl dark:bg-zinc-900">
            <h2 className="text-base font-semibold">{t("app.title")}</h2>
            <p className="text-zinc-500">
              {t("menu.version", { version: system?.version ?? "?" })}
            </p>
            <p>{t("menu.about_text")}</p>
            <p className="text-xs text-zinc-500">{t("menu.about_data")}</p>
            <div className="flex justify-end">
              <Button onClick={() => setAbout(false)}>{t("common.close")}</Button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
