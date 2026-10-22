import { useState } from "react";
import { api } from "../../api/client";
import { useT } from "../../i18n";
import { useApp } from "../../store";
import { DesignProgress } from "../DesignProgress";
import { PreviewDialog } from "../PreviewDialog";
import { ProgressList } from "../ProgressList";
import { Alert, Button, Section, StepBar } from "../ui";

export function PlanStep({ onBuild }: { onBuild: () => void }) {
  const { t } = useT();
  const job = useApp((s) => s.job);
  const setJob = useApp((s) => s.setJob);
  const backToList = async () => job && setJob(await api.reopen(job.id, "analyzed"));
  const [previewOpen, setPreviewOpen] = useState(false);
  const plan = job?.plan;
  if (job?.state === "planning") {
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium">{t("plan.designing")}</p>
        <p className="text-xs text-zinc-500">{t("plan.designing_hint")}</p>
        <DesignProgress />
        <ProgressList runningState="planning" hideStage={(s) => s.startsWith("design")} elapsed={false} />
      </div>
    );
  }
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
      <details className="text-sm" data-testid="plan-technical">
        <summary className="cursor-pointer text-xs text-zinc-500 select-none">{t("plan.technical")}</summary>
        <div className="mt-2 space-y-2 text-xs text-zinc-600 dark:text-zinc-300">
          <div>
            {t("plan.tile")}: <code>{plan.tile}</code>
          </div>
          <ul className="space-y-0.5">
            {plan.files.map((f) => (
              <li key={f}>
                <code>{f}</code>
              </li>
            ))}
          </ul>
        </div>
      </details>
      <StepBar back={{ label: t("steps.analysis"), onClick: backToList, testId: "back-to-list" }}>
        <Button variant="primary" onClick={onBuild} data-testid="plan-build">
          {t("plan.build")}
        </Button>
      </StepBar>
    </div>
  );
}
