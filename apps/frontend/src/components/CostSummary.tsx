import type { JobView } from "../api/client";
import { modelName } from "../ai";
import { useT } from "../i18n";
import { Section } from "./ui";

const STAGES = ["analysis", "rating", "similar", "design", "correction"] as const;
const usd = (v: number) => (v < 0.01 && v > 0 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
const tokens = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)} M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)} k` : `${n}`);

/** What this scenery cost in AI calls, from the token counts Anthropic reported. */
export function CostSummary({ job }: { job: JobView }) {
  const { t } = useT();
  const calls = job.costs ?? [];
  const total = calls.reduce((s, c) => s + c.usd, 0);
  const legacy = job.analysis?.ai?.cost_usd ?? 0; // jobs from before the per-call book
  if (!calls.length && legacy <= 0) {
    return (
      <Section title={t("cost.title")}>
        <p className="text-sm" data-testid="cost-total">
          {t("cost.none")}
        </p>
      </Section>
    );
  }
  if (!calls.length) {
    return (
      <Section title={t("cost.title")}>
        <p className="text-sm" data-testid="cost-total">
          {t("cost.total", { usd: usd(legacy) })}
        </p>
        <p className="text-xs text-zinc-500">{t("cost.legacy")}</p>
      </Section>
    );
  }
  const byStage = STAGES.map((stage) => {
    const list = calls.filter((c) => c.stage === stage);
    return {
      stage,
      count: list.length,
      usd: list.reduce((s, c) => s + c.usd, 0),
      input: list.reduce((s, c) => s + (c.input_tokens ?? 0) + (c.cache_read_tokens ?? 0) + (c.cache_write_tokens ?? 0), 0),
      output: list.reduce((s, c) => s + (c.output_tokens ?? 0), 0),
      items: [...new Set(list.map((c) => c.item).filter(Boolean))] as string[],
    };
  }).filter((r) => r.count > 0);
  const models = [...new Set(calls.map((c) => modelName(t, c.model)))];
  return (
    <Section title={t("cost.title")}>
      <p className="text-base font-semibold" data-testid="cost-total">
        {t("cost.total", { usd: usd(total) })}
      </p>
      <table className="w-full text-xs" data-testid="cost-table">
        <thead className="text-left text-zinc-500">
          <tr>
            <th className="font-normal">{t("cost.col_stage")}</th>
            <th className="text-right font-normal">{t("cost.col_calls")}</th>
            <th className="text-right font-normal">{t("cost.col_tokens")}</th>
            <th className="text-right font-normal">{t("cost.col_usd")}</th>
          </tr>
        </thead>
        <tbody>
          {byStage.map((r) => (
            <tr key={r.stage} className="border-t border-zinc-200 align-top dark:border-zinc-700">
              <td className="py-1">
                {t(`cost.stage.${r.stage}`)}
                {r.items.length > 0 && (
                  <div className="text-[10px] text-zinc-500">{r.items.slice(0, 6).join(", ")}{r.items.length > 6 ? " …" : ""}</div>
                )}
              </td>
              <td className="py-1 text-right">{r.count}</td>
              <td className="py-1 text-right whitespace-nowrap">
                {tokens(r.input)} / {tokens(r.output)}
              </td>
              <td className="py-1 text-right">{usd(r.usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-zinc-500">{t("cost.models", { models: models.join(", ") })}</p>
      <p className="text-xs text-zinc-500">{t("cost.accuracy")}</p>
    </Section>
  );
}
