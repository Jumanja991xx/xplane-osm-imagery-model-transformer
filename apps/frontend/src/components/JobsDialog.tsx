import { useEffect, useState } from "react";
import { api, type JobSummary } from "../api/client";
import { useT } from "../i18n";
import { useApp } from "../store";
import { Button, inputClass, Spinner } from "./ui";

export function useJobs(reload: unknown) {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  useEffect(() => {
    void api
      .listJobs()
      .then((list) => setJobs([...list].sort((a, b) => b.updated_at.localeCompare(a.updated_at))))
      .catch(() => setJobs([]));
  }, [reload]);
  return jobs;
}

export function JobRow({ job, onOpen }: { job: JobSummary; onOpen: (id: string) => void }) {
  const { t } = useT();
  return (
    <li className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
      <span className="min-w-0">
        <span className="block truncate font-medium">{job.package_name ?? t("menu.unnamed_job")}</span>
        <span className="text-xs text-zinc-500">
          {new Date(job.updated_at).toLocaleDateString()} · {t(`state.${job.state}`)} · {job.objects}{" "}
          {t("analysis.objects")}
        </span>
      </span>
      <Button onClick={() => onOpen(job.id)} data-testid={`open-job-${job.id}`}>
        {t("area.open")}
      </Button>
    </li>
  );
}

/** All jobs, newest first, with a filter: the "My jobs" window of the menu. */
export function JobsDialog() {
  const { t } = useT();
  const { setJob, setJobsOpen, setEditingBrief } = useApp();
  const jobs = useJobs(true);
  const [filter, setFilter] = useState("");
  const needle = filter.trim().toLowerCase();
  const shown = (jobs ?? []).filter((j) => !needle || (j.package_name ?? "").toLowerCase().includes(needle));
  const open = async (id: string) => {
    setEditingBrief(false);
    setJob(await api.getJob(id));
    setJobsOpen(false);
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <h2 className="text-base font-semibold">{t("menu.my_jobs")}</h2>
          <Button variant="ghost" onClick={() => setJobsOpen(false)}>
            {t("common.close")}
          </Button>
        </div>
        <div className="space-y-2 overflow-y-auto px-5 py-3">
          <input
            className={inputClass}
            value={filter}
            placeholder={t("area.filter_jobs")}
            onChange={(e) => setFilter(e.target.value)}
            data-testid="jobs-filter"
            autoFocus
          />
          {jobs === null ? (
            <Spinner />
          ) : shown.length === 0 ? (
            <p className="text-sm text-zinc-500">{t("menu.no_jobs")}</p>
          ) : (
            <ul className="divide-y divide-zinc-200 rounded-md ring-1 ring-zinc-200 dark:divide-zinc-700 dark:ring-zinc-700" data-testid="jobs-list">
              {shown.map((j) => (
                <JobRow key={j.id} job={j} onOpen={open} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
