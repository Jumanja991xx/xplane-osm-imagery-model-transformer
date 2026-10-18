import { useT } from "../i18n";
import { useApp } from "../store";
import { Spinner } from "./ui";

/** Progress events since the job last entered `runningState`. */
export function ProgressList({ runningState }: { runningState: string }) {
  const { t } = useT();
  const events = useApp((s) => s.events);
  let start = -1;
  events.forEach((e, i) => {
    if (e.type === "state" && e.state === runningState) start = i;
  });
  const progress = start >= 0 ? events.slice(start + 1).filter((e) => e.type === "progress") : [];
  const running = useApp((s) => s.job?.state === runningState);

  return (
    <ul className="space-y-1 text-sm" data-testid="progress">
      {progress.map((e, i) => {
        const last = i === progress.length - 1;
        return (
          <li key={e.seq} className="flex items-center gap-2">
            {last && running && (e.progress ?? 0) < 1 ? <Spinner /> : <span className="w-4 text-emerald-600">✓</span>}
            <span className="break-all">{t(e.key ?? "", e.params ?? undefined)}</span>
          </li>
        );
      })}
      {running && progress.length === 0 && (
        <li className="flex items-center gap-2">
          <Spinner />
        </li>
      )}
    </ul>
  );
}
