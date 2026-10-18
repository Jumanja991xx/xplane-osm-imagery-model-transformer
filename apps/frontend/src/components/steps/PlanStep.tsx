import { useT } from "../../i18n";
import { useApp } from "../../store";
import { ProgressList } from "../ProgressList";
import { Alert, Button, Section } from "../ui";

export function PlanStep({ onBuild }: { onBuild: () => void }) {
  const { t } = useT();
  const job = useApp((s) => s.job);
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
      <Section title={t("plan.confirmed")}>
        <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="plan-confirmed">
          {confirmed.map((item) => (
            <li key={item.label_key}>
              {item.count} × {t(item.label_key)}
            </li>
          ))}
        </ul>
        <Alert>{t("plan.m3_notice")}</Alert>
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
      <div className="flex justify-end">
        <Button variant="primary" onClick={onBuild} data-testid="plan-build">
          {t("plan.build")}
        </Button>
      </div>
    </div>
  );
}
