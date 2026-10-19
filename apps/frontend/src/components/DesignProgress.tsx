import { useEffect, useState } from "react";
import type { JobEvent } from "../api/client";
import { useT } from "../i18n";
import { useApp } from "../store";
import { formatUsd } from "./steps/AnalysisStep";
import { Spinner } from "./ui";

function clock(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

interface ObjectState {
  last: JobEvent;
  started?: number;
}

/** Live view of the AI architect at work: one line per object with what it is doing right
 * now, how long it has been working on it and what it cost; on top the totals. */
export function DesignProgress() {
  const { t } = useT();
  const events = useApp((s) => s.events);
  const running = useApp((s) => s.job?.state === "planning");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  let start = -1;
  events.forEach((e, i) => {
    if (e.type === "state" && e.state === "planning") start = i;
  });
  if (start < 0) return null;
  const since = events.slice(start);
  const startedAt = Date.parse(since[0].ts);
  let overall: JobEvent | undefined;
  const objects = new Map<string, ObjectState>();
  for (const e of since) {
    if (e.type !== "progress" || !e.stage) continue;
    if (e.stage === "design") overall = e;
    if (!e.stage.startsWith("design:")) continue;
    const o = objects.get(e.stage) ?? { last: e };
    if (e.params?.status === "running" && o.started == null) o.started = Date.parse(e.ts);
    o.last = e;
    objects.set(e.stage, o);
  }
  if (!overall && objects.size === 0) return null;
  const list = [...objects.values()];
  const done = list.filter((o) => o.last.params?.status === "done" || o.last.params?.status === "failed").length;
  const p = overall?.params ?? {};
  const lastTs = Math.max(...since.map((e) => Date.parse(e.ts)));

  return (
    <div className="space-y-2" data-testid="plan-design-progress">
      <div className="flex flex-wrap gap-x-4 gap-y-1 rounded-md bg-white p-2 text-xs ring-1 ring-zinc-200 dark:bg-zinc-800 dark:ring-zinc-700">
        <span className="font-medium">{t("design.progress.done", { done, total: list.length })}</span>
        <span>⏱ {t("design.progress.elapsed", { time: clock((running ? now : lastTs) - startedAt) })}</span>
        <span>
          💲{" "}
          {t("design.progress.cost", {
            cost: formatUsd(Number(p.cost_usd ?? 0)),
            max: formatUsd(Number(p.cost_max ?? 0)),
          })}
        </span>
      </div>
      <ul className="divide-y divide-zinc-200 rounded-md bg-white text-sm ring-1 ring-zinc-200 dark:divide-zinc-700 dark:bg-zinc-800 dark:ring-zinc-700">
        {list.map(({ last, started }) => {
          const params = last.params ?? {};
          const status = String(params.status ?? "waiting");
          const end = status === "done" || status === "failed" ? Date.parse(last.ts) : now;
          return (
            <li key={last.stage} className="flex items-start gap-2 px-2 py-1.5" data-testid="design-object">
              <span className="mt-0.5 w-4 shrink-0 text-center">
                {status === "running" && running ? (
                  <Spinner />
                ) : status === "done" ? (
                  <span className="text-emerald-600">✓</span>
                ) : status === "failed" ? (
                  <span className="text-amber-600">⚠</span>
                ) : (
                  <span className="text-zinc-400">⏳</span>
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="truncate font-medium">{String(params.name ?? "")}</span>
                  {started != null && (
                    <span className="ml-auto shrink-0 text-xs text-zinc-500">{clock(end - started)}</span>
                  )}
                  {params.cost_usd != null && (
                    <span className="shrink-0 text-xs text-zinc-500">{formatUsd(Number(params.cost_usd))} USD</span>
                  )}
                </div>
                <div className="text-xs text-zinc-600 dark:text-zinc-300">
                  {t(last.key ?? "", { ...params, error: params.error ? t(String(params.error)) : "" })}
                </div>
                {status === "done" && params.summary && (
                  <div className="text-xs italic text-zinc-500">„{String(params.summary)}“</div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
