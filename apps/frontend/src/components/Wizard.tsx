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
  const openGate = () => setGateOpen(true);

  return (
    <div className="flex h-full flex-col">
      <ol className="flex border-b border-zinc-200 px-3 dark:border-zinc-800">
        {STEP_KEYS.map((key, i) => (
          <li
            key={key}
            className={`flex-1 border-b-2 px-1 py-2 text-center text-xs font-medium ${
              i === step
                ? "border-accent-600 text-accent-700 dark:text-accent-100"
                : i < step
                  ? "border-transparent text-zinc-500"
                  : "border-transparent text-zinc-400 dark:text-zinc-600"
            }`}
          >
            <span className="mr-1 inline-flex h-5 w-5 items-center justify-center rounded-full bg-zinc-200 text-[10px] dark:bg-zinc-800">
              {i < step ? "✓" : i + 1}
            </span>
            <span className="hidden xl:inline">{t(key)}</span>
          </li>
        ))}
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
        <div className="flex flex-wrap justify-between gap-2 border-t border-zinc-200 px-4 py-2 dark:border-zinc-800">
          {canEditBrief ? (
            <Button variant="ghost" onClick={() => setEditingBrief(true)}>
              {t("common.edit_brief")}
            </Button>
          ) : (
            <span />
          )}
          {!["installed", "cancelled"].includes(job.state) && (
            <Button variant="ghost" onClick={async () => setJob(await api.cancel(job.id))}>
              {t("common.cancel_job")}
            </Button>
          )}
        </div>
      )}

      {gateOpen && job && gate && <GateDialog jobId={job.id} gate={gate} onClose={() => setGateOpen(false)} />}
    </div>
  );
}
