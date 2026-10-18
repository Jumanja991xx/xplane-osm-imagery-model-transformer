import { useT } from "../../i18n";
import { useApp } from "../../store";
import { ProgressList } from "../ProgressList";
import { Alert, Button } from "../ui";

export function InstallStep({ onInstall }: { onInstall: () => void }) {
  const { t } = useT();
  const { job, reset } = useApp();
  if (!job) return null;

  if (job.state === "cancelled") {
    return (
      <div className="space-y-3">
        <Alert kind="warning">{t("install.cancelled")}</Alert>
        <Button onClick={reset}>{t("common.new_job")}</Button>
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

  if (job.state === "installed" && job.install) {
    return (
      <div className="space-y-3" data-testid="install-done">
        <Alert kind="success">{t("install.done", { path: job.install.target_dir })}</Alert>
        {job.install.backup_dir && (
          <p className="text-xs break-all text-zinc-600 dark:text-zinc-300">
            {t("install.backup", { path: job.install.backup_dir })}
          </p>
        )}
        {job.plan && <Alert>{t(job.plan.test_scenery ? "install.test_hint" : "install.scene_hint")}</Alert>}
        <Button onClick={reset}>{t("common.new_job")}</Button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm">{t("install.intro")}</p>
      {job.build && <code className="block text-xs break-all text-zinc-600 dark:text-zinc-300">{job.build.package_dir}</code>}
      <div className="flex justify-end">
        <Button variant="primary" onClick={onInstall} data-testid="install-button">
          {t("install.button")}
        </Button>
      </div>
    </div>
  );
}
