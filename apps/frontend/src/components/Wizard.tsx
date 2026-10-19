import { useState } from "react";
import { api, type JobState } from "../api/client";
import { useJobEvents } from "../hooks/useJobEvents";
import { useT } from "../i18n";
import { useApp } from "../store";
import { GateDialog } from "./GateDialog";
import { AnalysisStep } from "./steps/AnalysisStep";
import { AreaStep } from "./steps/AreaStep";
import { BriefStep } from "./steps/BriefStep";
import { InstallStep } from "./steps/InstallStep";
import { PlanStep } from "./steps/PlanStep";
import { Alert, Button } from "./ui";

const STEP_KEYS = ["steps.area", "steps.brief", "steps.analysis", "steps.plan", "steps.install"];

const STEP_FOR_STATE: Record<JobState, number> = {
  draft: 1,
  clarified: 1,
  analyzing: 2,
  analyzed: 2,
  planned: 3,
  building: 3,
  built: 4,
  installing: 4,
  installed: 4,
  failed: 1,
  cancelled: 4,
};

const RUNNING: JobState[] = ["analyzing", "building", "installing"];

export function Wizard() {
  const { t } = useT();
  const { job, editingBrief, setEditingBrief, setJob } = useApp();
  const [gateOpen, setGateOpen] = useState(false);
  useJobEvents(job?.id);

  const step = !job ? 0 : editingBrief || job.state === "draft" ? 1 : STEP_FOR_STATE[job.state];
  const gate = job?.pending_gate ?? null;
  const running = job ? RUNNING.includes(job.state) : false;
  const canEditBrief = job && !running && !editingBrief && !["draft", "installed", "cancelled"].includes(job.state);
  const [confirmCancel, setConfirmCancel] = useState(false);
  // Earlier steps can be revisited without losing the analysis and the edited list.
  const canGoBack = (i: number) =>
    Boolean(job && !running && i < step && (i === 1 ? job.brief : i === 2 ? job.analysis : false));
  const goBack = async (i: number) => {
    if (!job || !canGoBack(i)) return;
    if (i === 1) setEditingBrief(true);
    if (i === 2) setJob(await api.reopen(job.id, "analyzed"));
  };
  // Always show the current summary: settings may have changed since the job was loaded.
  const openGate = async () => {
    if (job) {
      try {
        setJob(await api.getJob(job.id));
      } catch {
        /* the dialog still shows the last known gate; approving re-validates it */
      }
    }
    setGateOpen(true);
  };

  return (
    <div className="flex h-full flex-col">
      <ol className="flex border-b border-zinc-200 px-3 dark:border-zinc-800">
        {STEP_KEYS.map((key, i) => {
          const back = canGoBack(i);
          return (
            <li key={key} className="flex flex-1">
              <button
                type="button"
                disabled={!back}
                onClick={() => goBack(i)}
                title={back ? t("steps.back_to", { step: t(key) }) : undefined}
                data-testid={`step-${i}`}
                className={`flex flex-1 flex-col items-center gap-1 border-b-2 px-0.5 py-2 text-center ${
                  i === step
                    ? "border-accent-600 text-accent-700 dark:text-accent-100"
                    : i < step
                      ? "border-transparent text-zinc-500 hover:text-accent-700 disabled:hover:text-zinc-500"
                      : "border-transparent text-zinc-400 dark:text-zinc-600"
                } ${back ? "cursor-pointer" : "cursor-default"}`}
              >
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold ${
                    i === step ? "bg-accent-600 text-white" : "bg-zinc-200 dark:bg-zinc-800"
                  }`}
                >
                  {i < step ? "✓" : i + 1}
                </span>
                <span className="text-[11px] leading-tight font-medium">{t(key)}</span>
              </button>
            </li>
          );
        })}
      </ol>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <h2 className="text-lg font-semibold">{t(STEP_KEYS[step])}</h2>

        {job?.error && (
          <Alert kind="error">
            <div className="font-medium">{t(job.error.key)}</div>
            {job.error.detail && <pre className="mt-1 text-xs break-all whitespace-pre-wrap">{job.error.detail}</pre>}
          </Alert>
        )}

        {step === 0 && <AreaStep />}
        {step === 1 && (editingBrief || job?.state === "draft" || !job?.brief) && (
          <BriefStep onSubmitted={openGate} />
        )}
        {step === 1 && !editingBrief && job?.state === "clarified" && (
          <div className="space-y-3">
            <p className="text-sm text-zinc-600 dark:text-zinc-300">{t("brief.intro")}</p>
            <div className="flex justify-end">
              <Button variant="primary" onClick={openGate} disabled={!gate} data-testid="open-gate-a">
                {t("brief.submit")}
              </Button>
            </div>
          </div>
        )}
        {step === 2 && <AnalysisStep onConfirm={openGate} />}
        {step === 3 && <PlanStep onBuild={openGate} />}
        {step === 4 && <InstallStep onInstall={openGate} />}
      </div>

      {job && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200 px-4 py-2 dark:border-zinc-800">
          {canEditBrief ? (
            <Button variant="ghost" onClick={() => setEditingBrief(true)}>
              {t("common.edit_brief")}
            </Button>
          ) : job.state === "cancelled" && job.analysis ? (
            <Button variant="ghost" onClick={async () => setJob(await api.reopen(job.id, "analyzed"))} data-testid="resume-job">
              ↩ {t("common.resume_job")}
            </Button>
          ) : (
            <span />
          )}
          {!["installed", "cancelled"].includes(job.state) &&
            (confirmCancel ? (
              <span className="flex items-center gap-2 text-xs">
                {t("common.cancel_confirm")}
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
                <Button variant="ghost" onClick={() => setConfirmCancel(false)}>
                  {t("common.no")}
                </Button>
              </span>
            ) : (
              <button
                type="button"
                className="text-xs text-zinc-500 underline hover:text-red-600"
                onClick={() => setConfirmCancel(true)}
                data-testid="cancel-job"
              >
                {t("common.cancel_job")}
              </button>
            ))}
        </div>
      )}

      {gateOpen && job && gate && <GateDialog jobId={job.id} gate={gate} onClose={() => setGateOpen(false)} />}
    </div>
  );
}
