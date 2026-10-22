import { useState } from "react";
import { api, ApiError, type GateItem, type PendingGate } from "../api/client";
import { modelName } from "../ai";
import { useT, type Translate } from "../i18n";
import { useApp } from "../store";
import { Alert, Button, Spinner } from "./ui";

/** Some params are ids that need their own translation (e.g. the model name). */
export function gateItemText(t: Translate, item: GateItem): string {
  const params: Record<string, unknown> = { ...item.params };
  if (typeof params.model === "string") params.model = modelName(t, params.model);
  if (typeof params.label === "string") params.label = t(params.label);
  return t(item.key, params);
}

export function GateDialog({
  jobId,
  gate,
  onClose,
}: {
  jobId: string;
  gate: PendingGate;
  onClose: () => void;
}) {
  const { t } = useT();
  const setJob = useApp((s) => s.setJob);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      setJob(await api.approve(jobId, gate.gate, gate.hash));
      onClose();
    } catch (e) {
      if (e instanceof ApiError) {
        setError(t(e.key, e.params));
        // The summary changed underneath us: show the new one; the user decides again.
        if (e.key === "error.gate_stale" || e.key === "error.gate_blocked") {
          setJob(await api.getJob(jobId));
        }
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const of = (kind: GateItem["kind"]) => gate.items.filter((i) => i.kind === kind);
  const list = (items: GateItem[], className = "") => (
    <ul className={`space-y-1 text-sm ${className}`}>
      {items.map((item, i) => (
        <li key={i} className="break-words">
          {gateItemText(t, item)}
        </li>
      ))}
    </ul>
  );
  const details = [...of("write"), ...of("info")];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <div className="border-b border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <h2 className="text-base font-semibold">{t(`gate.title.${gate.gate}`)}</h2>
        </div>
        <div className="max-h-[60vh] space-y-4 overflow-y-auto px-5 py-4">
          {of("warning").length > 0 && (
            <Alert kind="warning">{list(of("warning"), "list-disc pl-4")}</Alert>
          )}
          {of("action").length > 0 && (
            <div>
              <div className="mb-1 text-xs font-semibold tracking-wide text-zinc-500 uppercase">{t("gate.kind.action")}</div>
              {list(of("action"), "list-disc pl-4")}
            </div>
          )}
          {of("cost").length > 0 && (
            <div className="rounded-md bg-zinc-100 px-3 py-2 dark:bg-zinc-800">
              <div className="mb-1 text-xs font-semibold tracking-wide text-zinc-500 uppercase">{t("gate.kind.cost")}</div>
              {list(of("cost"))}
            </div>
          )}
          {details.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-xs text-zinc-500 select-none">{t("gate.details")}</summary>
              {list(details, "mt-2 text-zinc-600 dark:text-zinc-300")}
            </details>
          )}
          {gate.blocked && <Alert kind="warning">{t("gate.blocked")}</Alert>}
          {error && <Alert kind="error">{error}</Alert>}
        </div>
        <div className="flex justify-end gap-2 border-t border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <Button onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={approve} disabled={busy || gate.blocked} data-testid="gate-approve">
            {busy && <Spinner />}
            {t(`gate.approve.${gate.gate}`)}
          </Button>
        </div>
      </div>
    </div>
  );
}
