import { useState } from "react";
import { api } from "../../api/client";
import { useT } from "../../i18n";
import { useApp } from "../../store";
import { PreviewDialog } from "../PreviewDialog";
import { ProgressList } from "../ProgressList";
import { Alert, Button, Section } from "../ui";

export function PlanStep({ onBuild }: { onBuild: () => void }) {
  const { t } = useT();
  const job = useApp((s) => s.job);
  const setJob = useApp((s) => s.setJob);
  const backToList = async () => job && setJob(await api.reopen(job.id, "analyzed"));
  const [previewOpen, setPreviewOpen] = useState(false);
  const plan = job?.plan;
  if (!job || !plan) return null;
  const confirmed = plan.items.filter((i) => i.kind === "confirmed");
  const built = plan.items.filter((i) => i.kind !== "confirmed");

  if (job.state === "building") {
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium">{t("plan.building")}</p>
        <ProgressList runningState="building" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {previewOpen && <PreviewDialog jobId={job.id} editable={job.state === "planned"} onClose={() => setPreviewOpen(false)} />}
      <Section title={t("plan.confirmed")}>
        <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="plan-confirmed">
          {confirmed.map((item) => (
            <li key={item.label_key}>
              {item.count} × {t(item.label_key)}
            </li>
          ))}
        </ul>
      </Section>
      <Section title={t("preview.title")}>
        <p className="text-sm text-zinc-600 dark:text-zinc-300">{t("plan.preview_hint")}</p>
        {(plan.objects ?? []).some((o) => o.status === "error") && (
          <Alert kind="warning">
            {t("gate.c.objects_failed", { count: (plan.objects ?? []).filter((o) => o.status === "error").length })}
          </Alert>
        )}
        <Button onClick={() => setPreviewOpen(true)} data-testid="open-preview">
          🧊 {t("plan.open_preview")}
        </Button>
      </Section>
      <Section title={t("plan.intro")}>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {built.map((item) => (
            <li key={item.kind}>{t(item.label_key, { count: item.count, ...item.params })}</li>
          ))}
        </ul>
      </Section>
      <Section title={t("plan.tile")}>
        <code className="text-xs">{plan.tile}</code>
      </Section>
      <Section title={t("plan.files")}>
        <ul className="space-y-0.5 text-xs text-zinc-600 dark:text-zinc-300">
          {plan.files.map((f) => (
            <li key={f}>
              <code>{f}</code>
            </li>
          ))}
        </ul>
      </Section>
      <div className="sticky -bottom-4 z-10 -mx-4 flex justify-end gap-2 border-t border-zinc-200 bg-zinc-50/95 px-4 py-2 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/95">
        <Button variant="ghost" onClick={backToList} data-testid="back-to-list">
          ← {t("common.back_to_list")}
        </Button>
        <Button variant="primary" onClick={onBuild} data-testid="plan-build">
          {t("plan.build")}
        </Button>
      </div>
    </div>
  );
}
