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
import { Alert } from "./ui";

const STEP_KEYS = ["steps.area", "steps.brief", "steps.analysis", "steps.plan", "steps.install"];

const STEP_FOR_STATE: Record<JobState, number> = {
  draft: 1,
  clarified: 1,
  analyzing: 2,
  analyzed: 2,
  planning: 3,
  planned: 3,
  building: 3,
  built: 4,
  installing: 4,
  installed: 4,
  failed: 1,
  cancelled: 4,
};

const RUNNING: JobState[] = ["analyzing", "planning", "building", "installing"];

export function Wizard() {
  const { t } = useT();
  const { job, editingBrief, setEditingBrief, setJob, reset, setAoi } = useApp();
  const [gateOpen, setGateOpen] = useState(false);
  useJobEvents(job?.id);

  const step = !job ? 0 : editingBrief || job.state === "draft" || job.state === "clarified" ? 1 : STEP_FOR_STATE[job.state];
  const gate = job?.pending_gate ?? null;
  const running = job ? RUNNING.includes(job.state) : false;
  // Every earlier step can be revisited; nothing is lost on the way back (the list
  // stays, the plan and its designs stay when only the build is redone).
  const canGoBack = (i: number) =>
    Boolean(
      job &&
        !running &&
        i < step &&
        (i === 1 ? job.brief : i === 2 ? job.analysis : i === 3 ? job.plan && job.state !== "planned" : false),
    );
  const goBack = async (i: number) => {
    if (!job || !canGoBack(i)) return;
    if (i === 1) setEditingBrief(true);
    if (i === 2) setJob(await api.reopen(job.id, "analyzed"));
    if (i === 3) setJob(await api.reopen(job.id, "planned"));
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
  // From the wishes of a new job back to the map: draw the area again.
  const backToArea = () => {
    const area = job?.aoi ?? null;
    reset();
    if (area) setAoi(area);
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
                    i === step ? "bg-accent-600 text-white" : i < step ? "bg-accent-100 text-accent-700 dark:bg-accent-700/40 dark:text-accent-100" : "bg-zinc-200 dark:bg-zinc-800"
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
        <h2 className="text-lg font-semibold">{t(`${STEP_KEYS[step]}_title`)}</h2>

        {job?.error && (
          <Alert kind="error">
            <div className="font-medium">{t(job.error.key)}</div>
            {job.error.detail && <pre className="mt-1 text-xs break-all whitespace-pre-wrap">{job.error.detail}</pre>}
          </Alert>
        )}

        {step === 0 && <AreaStep />}
        {step === 1 && (
          <BriefStep
            onSubmitted={openGate}
            back={
              job?.analysis
                ? {
                    // Changed nothing: back to where the job was (the list after "analyse again").
                    label: job.state === "clarified" ? t("steps.analysis") : t("common.cancel"),
                    onClick: async () => {
                      setEditingBrief(false);
                      if (job.state === "clarified") setJob(await api.reopen(job.id, "analyzed"));
                    },
                    testId: "brief-cancel",
                  }
                : { label: t("steps.area"), onClick: backToArea, testId: "back-to-area" }
            }
          />
        )}
        {step === 2 && (
          <AnalysisStep
            onConfirm={openGate}
            onBack={() => setEditingBrief(true)}
            onReanalyze={async () => {
              if (!job) return;
              setJob(await api.reopen(job.id, "clarified"));
              setGateOpen(true);
            }}
          />
        )}
        {step === 3 && <PlanStep onBuild={openGate} />}
        {step === 4 && <InstallStep onInstall={openGate} />}
      </div>

      {gateOpen && job && gate && <GateDialog jobId={job.id} gate={gate} onClose={() => setGateOpen(false)} />}
    </div>
  );
}
