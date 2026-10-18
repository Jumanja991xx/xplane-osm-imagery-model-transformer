import { useState } from "react";
import { api, ApiError } from "../../api/client";
import { useT } from "../../i18n";
import { useApp } from "../../store";
import { ProgressList } from "../ProgressList";
import { Alert, Button, Section } from "../ui";

export function AnalysisStep({ onConfirm }: { onConfirm: () => void }) {
  const { t } = useT();
  const { job, setJob } = useApp();
  const [error, setError] = useState<string | null>(null);
  if (!job) return null;

  if (job.state === "analyzing") {
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium">{t("analysis.running")}</p>
        <ProgressList runningState="analyzing" />
      </div>
    );
  }

  const analysis = job.analysis;
  if (!analysis) return null;

  const answer = async (questionId: string, value: string) => {
    setError(null);
    try {
      setJob(await api.postAnswers(job.id, { [questionId]: value }));
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    }
  };

  return (
    <div className="space-y-4">
      {analysis.notes?.map((n) => (
        <Alert key={n}>{t(n)}</Alert>
      ))}
      <Section title={t("analysis.objects")}>
        <p className="text-sm">{analysis.objects?.length ? analysis.objects.length : t("analysis.none")}</p>
      </Section>
      <Section title={t("analysis.surfaces")}>
        <p className="text-sm">{analysis.surfaces?.length ? analysis.surfaces.length : t("analysis.none")}</p>
      </Section>
      <Section title={t("analysis.questions")}>
        {!analysis.questions?.length && <p className="text-sm">{t("analysis.none")}</p>}
        {analysis.questions?.map((q) => (
          <fieldset key={q.id} className="space-y-1.5 rounded-md bg-white p-3 ring-1 ring-zinc-200 dark:bg-zinc-800 dark:ring-zinc-700">
            <legend className="sr-only">{t(q.text_key, q.params)}</legend>
            <p className="text-sm font-medium">{t(q.text_key, q.params)}</p>
            <div className="flex flex-wrap gap-3">
              {q.options.map((o) => (
                <label key={o.value} className="flex items-center gap-1.5 text-sm">
                  <input
                    type="radio"
                    name={q.id}
                    checked={job.answers?.[q.id] === o.value}
                    onChange={() => answer(q.id, o.value)}
                    data-testid={`answer-${q.id}-${o.value}`}
                  />
                  {t(o.label_key, o.params)}
                  {q.suggested === o.value && (
                    <span className="rounded bg-accent-50 px-1.5 text-xs text-accent-700 dark:bg-accent-700/30 dark:text-accent-100">
                      {t("analysis.suggested")}
                    </span>
                  )}
                </label>
              ))}
            </div>
          </fieldset>
        ))}
      </Section>
      {error && <Alert kind="error">{error}</Alert>}
      <div className="flex justify-end">
        <Button variant="primary" onClick={onConfirm} data-testid="analysis-confirm">
          {t("analysis.confirm")}
        </Button>
      </div>
    </div>
  );
}
