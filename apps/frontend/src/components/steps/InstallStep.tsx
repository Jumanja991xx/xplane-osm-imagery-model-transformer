import { useState } from "react";
import { api, ApiError, type JobView } from "../../api/client";
import { useT } from "../../i18n";
import { folderName, validPackageName } from "../../names";
import { useApp } from "../../store";
import { PreviewDialog } from "../PreviewDialog";
import { ProgressList } from "../ProgressList";
import { Alert, Button, inputClass, Section } from "../ui";

export function InstallStep({ onInstall }: { onInstall: () => void }) {
  const { t } = useT();
  const { job, reset, setJob } = useApp();
  const [previewOpen, setPreviewOpen] = useState(false);
  if (!job) return null;
  const backToList = async () => setJob(await api.reopen(job.id, "analyzed"));
  const back = job.analysis && (
    <Button variant="ghost" onClick={backToList} data-testid="back-to-list">
      ← {t("common.back_to_list")}
    </Button>
  );

  if (job.state === "cancelled") {
    return (
      <div className="space-y-3">
        <Alert kind="warning">{t("install.cancelled")}</Alert>
        <div className="flex gap-2">
          {back}
          <Button onClick={reset}>{t("common.new_job")}</Button>
        </div>
      </div>
    );
  }

  if (job.state === "installing") {
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium">{t("install.installing")}</p>
        <ProgressList runningState="installing" />
      </div>
    );
  }

  const previewButton = job.plan && !job.plan.test_scenery && (
    <>
      {previewOpen && <PreviewDialog jobId={job.id} onClose={() => setPreviewOpen(false)} />}
      <Button onClick={() => setPreviewOpen(true)} data-testid="result-preview">
        🧊 {t("install.preview")}
      </Button>
    </>
  );

  if (job.state === "installed" && job.install) {
    return (
      <div className="space-y-3" data-testid="install-done">
        {previewButton}
        <Alert kind="success">{t("install.done", { path: job.install.target_dir })}</Alert>
        {job.install.backup_dir && (
          <p className="text-xs break-all text-zinc-600 dark:text-zinc-300">
            {t("install.backup", { path: job.install.backup_dir })}
          </p>
        )}
        {job.plan && <Alert>{t(job.plan.test_scenery ? "install.test_hint" : "install.scene_hint")}</Alert>}
        <p className="text-xs text-zinc-500">{t("install.change_hint")}</p>
        <div className="flex gap-2">
          {back}
          <Button onClick={reset}>{t("common.new_job")}</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm">{t("install.intro")}</p>
      {previewButton}
      {job.state === "built" && job.plan && <PackageName job={job} />}
      {job.build && <code className="block text-xs break-all text-zinc-600 dark:text-zinc-300">{job.build.package_dir}</code>}
      {job.build && job.build.meshes > 0 && (
        <p className="text-xs" data-testid="build-blender">
          {job.build.blender ? (
            <span className={job.build.shaded ? "text-emerald-600" : "text-amber-600"}>
              {t(job.build.shaded ? "install.blender_done" : "install.blender_failed", {
                version: job.build.blender,
                shaded: job.build.shaded,
                total: job.build.meshes,
                detailed: job.build.detailed ?? 0,
              })}
            </span>
          ) : (
            <span className="text-zinc-500">{t("install.blender_none")}</span>
          )}
        </p>
      )}
      <div className="flex justify-end gap-2">
        {back}
        <Button variant="primary" onClick={onInstall} data-testid="install-button">
          {t("install.button")}
        </Button>
      </div>
    </div>
  );
}

/** After the build: the user may still rename the scenery folder before installing. */
function PackageName({ job }: { job: JobView }) {
  const { t } = useT();
  const setJob = useApp((s) => s.setJob);
  const current = job.plan?.package_name ?? "";
  const [name, setName] = useState(current);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const items = job.analysis?.items ?? [];
  const main =
    (job.design_targets ?? []).map((id) => items.find((i) => i.id === id)?.name).find(Boolean) ??
    items.find((i) => i.kind === "object" && i.research && i.name)?.name ??
    items.find((i) => i.kind === "object" && i.name)?.name;
  const suggestion = main ? folderName(main) : "";
  const valid = validPackageName(name);
  const rename = async (value: string) => {
    setBusy(true);
    setError(null);
    try {
      setJob(await api.renamePackage(job.id, value.trim()));
      setName(value.trim());
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title={t("install.name_title")}>
      <p className="text-xs text-zinc-600 dark:text-zinc-300">{t("install.name_hint")}</p>
      <div className="flex gap-2">
        <input
          className={inputClass}
          value={name}
          maxLength={60}
          onChange={(e) => setName(e.target.value)}
          data-testid="package-name"
        />
        <Button
          onClick={() => rename(name)}
          disabled={busy || !valid || name.trim() === current}
          data-testid="package-rename"
        >
          {t("install.rename")}
        </Button>
      </div>
      {!valid && name.trim() !== "" && <p className="text-xs text-amber-600">{t("install.name_invalid")}</p>}
      {suggestion && suggestion !== current && validPackageName(suggestion) && (
        <Button variant="ghost" onClick={() => rename(suggestion)} disabled={busy} data-testid="package-suggestion">
          💡 {t("install.name_suggestion", { name: suggestion })}
        </Button>
      )}
      {error && <Alert kind="error">{error}</Alert>}
    </Section>
  );
}
