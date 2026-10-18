import { useState } from "react";
import { api, ApiError, type GateItem, type PendingGate } from "../api/client";
import { useT, type Translate } from "../i18n";
import { useApp } from "../store";
import { Alert, Button, Spinner } from "./ui";

const KIND_ORDER: GateItem["kind"][] = ["action", "cost", "write", "warning", "info"];
const KIND_ICON: Record<GateItem["kind"], string> = {
  action: "▶",
  cost: "$",
  write: "📁",
  warning: "⚠",
  info: "ℹ",
};

/** Some params are ids that need their own translation (e.g. the model name). */
export function gateItemText(t: Translate, item: GateItem): string {
  const params: Record<string, unknown> = { ...item.params };
  if (typeof params.model === "string") params.model = t(`model.${params.model}`);
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <div className="border-b border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <h2 className="text-base font-semibold">{t(`gate.title.${gate.gate}`)}</h2>
        </div>
        <div className="max-h-[60vh] space-y-4 overflow-y-auto px-5 py-4">
          {KIND_ORDER.map((kind) => {
            const items = gate.items.filter((i) => i.kind === kind);
            if (!items.length) return null;
            return (
              <div key={kind}>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  {KIND_ICON[kind]} {t(`gate.kind.${kind}`)}
                </div>
                <ul className="space-y-1">
                  {items.map((item, i) => (
                    <li
                      key={i}
                      className={`text-sm break-words ${kind === "warning" ? "text-amber-700 dark:text-amber-300" : ""}`}
                    >
                      {gateItemText(t, item)}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          {gate.blocked && <Alert kind="warning">{t("gate.blocked")}</Alert>}
          {error && <Alert kind="error">{error}</Alert>}
        </div>
        <div className="flex justify-end gap-2 border-t border-zinc-200 px-5 py-3 dark:border-zinc-700">
          <Button onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={approve} disabled={busy || gate.blocked} data-testid="gate-approve">
            {busy && <Spinner />}
            {t("gate.approve")}
          </Button>
        </div>
      </div>
    </div>
  );
}
