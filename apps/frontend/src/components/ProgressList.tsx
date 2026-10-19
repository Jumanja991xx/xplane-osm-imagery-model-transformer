import { useEffect, useState } from "react";
import { useT } from "../i18n";
import { useApp } from "../store";
import { Spinner } from "./ui";

/** Steps that usually take long enough that a word about the expected time helps. */
const HINTED = new Set([
  "osm",
  "imagery",
  "detect",
  "ai",
  "research",
  "plan",
  "write",
  "compile",
  "verify",
  "backup",
  "copy",
]);
const SLOW_AFTER_MS = 90_000;

export function clock(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Progress events since the job last entered `runningState`: one line per stage with
 * its time, the usual duration of the running step and a word when it takes long. */
export function ProgressList({
  runningState,
  hideStage,
  elapsed = true,
}: {
  runningState: string;
  hideStage?: (stage: string) => boolean;
  elapsed?: boolean; // the total time on top (off where another view shows it)
}) {
  const { t } = useT();
  const events = useApp((s) => s.events);
  const running = useApp((s) => s.job?.state === runningState);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  let start = -1;
  events.forEach((e, i) => {
    if (e.type === "state" && e.state === runningState) start = i;
  });
  const all =
    start >= 0
      ? events
          .slice(start + 1)
          .filter((e) => e.type === "progress" && !(hideStage && e.stage && hideStage(e.stage)))
      : [];
  // One line per stage, showing its latest state (no repeated lines per tile/batch).
  const latest = new Map<string, (typeof all)[number]>();
  const first = new Map<string, number>();
  for (const e of all) {
    const key = e.stage ?? String(e.seq);
    latest.set(key, e);
    if (!first.has(key)) first.set(key, Date.parse(e.ts));
  }
  const progress = [...latest.entries()];
  const startedAt = start >= 0 ? Date.parse(events[start].ts) : now;
  const lastTs = all.length ? Date.parse(all[all.length - 1].ts) : startedAt;

  return (
    <div className="space-y-2">
      {start >= 0 && elapsed && (
        <p className="text-xs text-zinc-500" data-testid="progress-elapsed">
          ⏱ {t("progress.elapsed", { time: clock((running ? now : lastTs) - startedAt) })}
        </p>
      )}
      <ul className="space-y-1.5 text-sm" data-testid="progress">
        {progress.map(([stage, e], i) => {
          const last = i === progress.length - 1;
          const active = last && running && (e.progress ?? 0) < 1;
          const began = first.get(stage) ?? startedAt;
          // A finished line ends where the next one began.
          const next = progress[i + 1];
          const end = active ? now : next ? (first.get(next[0]) ?? lastTs) : Date.parse(e.ts);
          const took = end - began;
          return (
            <li key={e.seq} className="flex items-start gap-2">
              <span className="mt-0.5 w-4 shrink-0">
                {active ? <Spinner /> : <span className="text-emerald-600">✓</span>}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="break-words">
                    {t(e.key ?? "", e.params ?? undefined)}
                    {(e.progress ?? 0) > 0 && (e.progress ?? 0) < 1 && (
                      <span className="ml-1 text-xs text-zinc-500">{Math.round((e.progress ?? 0) * 100)} %</span>
                    )}
                  </span>
                  {(active || took >= 1000) && (
                    <span className="ml-auto shrink-0 text-xs text-zinc-500 tabular-nums">{clock(took)}</span>
                  )}
                </div>
                {active && HINTED.has(stage) && (
                  <p className="text-xs text-zinc-500" data-testid="progress-hint">
                    {t(e.key === "stage.write_blender" ? "progress.hint.write_blender" : `progress.hint.${stage}`)}
                  </p>
                )}
                {active && took > SLOW_AFTER_MS && (
                  <p className="text-xs text-amber-600" data-testid="progress-slow">
                    {t("progress.slow")}
                  </p>
                )}
              </div>
            </li>
          );
        })}
        {running && progress.length === 0 && !hideStage && (
          <li className="flex items-center gap-2">
            <Spinner />
          </li>
        )}
      </ul>
    </div>
  );
}
