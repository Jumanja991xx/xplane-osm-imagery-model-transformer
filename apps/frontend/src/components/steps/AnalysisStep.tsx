import { useEffect, useRef, useState } from "react";
import { api, ApiError, type ItemPatch, type JobView, type Question, type SceneItem } from "../../api/client";
import { useT, type Translate } from "../../i18n";
import { CATEGORY_COLORS, categoryLabelKey, draftFor, OBJECT_CATEGORIES, SURFACE_TYPES } from "../../scene";
import { useApp } from "../../store";
import { ProgressList } from "../ProgressList";
import { Alert, Button, Checkbox, inputClass, Section } from "../ui";

const WARNING_NOTES = new Set([
  "imagery.failed",
  "imagery.not_covered",
  "imagery.custom_missing",
  "analysis.ai_limit_reached",
  "analysis.ai_auth_failed",
  "analysis.ai_call_failed",
  "analysis.ai_fallback_model",
  "analysis.nothing_found",
]);

function itemTitle(t: Translate, item: SceneItem): string {
  return item.name || t(categoryLabelKey(item));
}

function questionText(t: Translate, q: Question): string {
  return q.text ?? t(q.text_key ?? "", q.params);
}

export function AnalysisStep({ onConfirm }: { onConfirm: () => void }) {
  const { t } = useT();
  const { job, setJob } = useApp();
  const [error, setError] = useState<string | null>(null);
  // Shown immediately; the server copy follows a moment later.
  const [picked, setPicked] = useState<Record<string, string>>({});
  if (!job) return null;

  if (job.state === "analyzing") {
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium">{t("analysis.running")}</p>
        <ProgressList runningState="analyzing" />
      </div>
    );
  }

  const analysis = job.analysis;
  if (!analysis) return null;
  const editable = job.state === "analyzed";

  const run = async (fn: () => Promise<JobView>) => {
    setError(null);
    try {
      setJob(await fn());
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    }
  };

  const answer = async (answers: Record<string, string>) => {
    setPicked((p) => ({ ...p, ...answers }));
    await run(() => api.postAnswers(job.id, answers));
  };

  const open = (analysis.questions ?? []).filter((q) => !(job.answers ?? {})[q.id]);
  const acceptAll = () =>
    answer(Object.fromEntries(open.filter((q) => q.suggested).map((q) => [q.id, q.suggested!])));

  return (
    <div className="space-y-4">
      {analysis.notes?.map((n, i) => (
        <Alert key={i} kind={WARNING_NOTES.has(n.key) ? "warning" : "info"}>
          {t(n.key, n.params)}
        </Alert>
      ))}

      <SourcesSummary job={job} />

      {(analysis.questions?.length ?? 0) > 0 && (
        <Section title={t("analysis.questions")}>
          {open.length > 1 && editable && (
            <Button onClick={acceptAll} data-testid="accept-suggestions">
              {t("analysis.accept_suggestions", { count: open.length })}
            </Button>
          )}
          {analysis.questions!.map((q) => (
            <fieldset
              key={q.id}
              className="space-y-1.5 rounded-md bg-white p-3 ring-1 ring-zinc-200 dark:bg-zinc-800 dark:ring-zinc-700"
            >
              <legend className="sr-only">{questionText(t, q)}</legend>
              <p className="text-sm font-medium">{questionText(t, q)}</p>
              {q.item_id && <ItemLink itemId={q.item_id} />}
              <div className="flex flex-wrap gap-3">
                {q.options.map((o) => (
                  <label key={o.value} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="radio"
                      name={q.id}
                      disabled={!editable}
                      checked={(picked[q.id] ?? job.answers?.[q.id]) === o.value}
                      onChange={() => answer({ [q.id]: o.value })}
                      data-testid={`answer-${q.id}-${o.value}`}
                    />
                    {o.label ?? t(o.label_key ?? "", o.params)}
                    {q.suggested === o.value && (
                      <span className="rounded bg-accent-50 px-1.5 text-xs text-accent-700 dark:bg-accent-700/30 dark:text-accent-100">
                        {t("analysis.suggested")}
                      </span>
                    )}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
        </Section>
      )}

      <ItemList job={job} editable={editable} run={run} />

      {error && <Alert kind="error">{error}</Alert>}
      {editable && (
        <div className="flex justify-end">
          <Button variant="primary" onClick={onConfirm} data-testid="analysis-confirm">
            {t("analysis.confirm")}
          </Button>
        </div>
      )}
    </div>
  );
}

function SourcesSummary({ job }: { job: JobView }) {
  const { t } = useT();
  const { imageryVisible, setImageryVisible } = useApp();
  const analysis = job.analysis!;
  const imagery = analysis.imagery;
  const ai = analysis.ai;
  return (
    <div className="space-y-1.5 rounded-md bg-white p-3 text-xs ring-1 ring-zinc-200 dark:bg-zinc-800 dark:ring-zinc-700">
      <div>{t("analysis.source_osm")}</div>
      {imagery ? (
        <div className="space-y-1">
          <div>
            {t("analysis.source_imagery", { source: imagery.source_name, gsd: imagery.gsd_m })}
            <div className="text-zinc-500">
              © {imagery.attribution} ({imagery.license})
            </div>
          </div>
          <Checkbox label={t("analysis.show_imagery")} checked={imageryVisible} onChange={setImageryVisible} />
        </div>
      ) : (
        <div className="text-zinc-500">{t("analysis.no_imagery")}</div>
      )}
      {ai && (
        <div data-testid="ai-summary">
          {t("analysis.ai_summary", {
            model: t(`model.${ai.model}`),
            analyzed: ai.analyzed ?? 0,
            cost: (ai.cost_usd ?? 0).toFixed(2),
          })}
        </div>
      )}
    </div>
  );
}

function ItemLink({ itemId }: { itemId: string }) {
  const { t } = useT();
  const { job, selectItem } = useApp();
  const item = job?.analysis?.items?.find((i) => i.id === itemId);
  if (!item) return null;
  return (
    <button className="text-xs text-accent-700 underline dark:text-accent-100" onClick={() => selectItem(itemId)}>
      {itemTitle(t, item)} – {t("analysis.show_on_map")}
    </button>
  );
}

function ItemList({
  job,
  editable,
  run,
}: {
  job: JobView;
  editable: boolean;
  run: (fn: () => Promise<JobView>) => Promise<void>;
}) {
  const { t } = useT();
  const { selectedItem, selectItem, itemDraft, startItemDraft } = useApp();
  const items = job.analysis?.items ?? [];
  const [newCategory, setNewCategory] = useState("object:buildings");

  const groups: { kind: "object" | "surface"; title: string }[] = [
    { kind: "object", title: t("analysis.objects") },
    { kind: "surface", title: t("analysis.surfaces") },
  ];

  return (
    <>
      {groups.map(({ kind, title }) => {
        const list = items.filter((i) => i.kind === kind);
        return (
          <Section key={kind} title={`${title} (${list.length})`}>
            {!list.length && <p className="text-sm">{t("analysis.none")}</p>}
            <ul className="divide-y divide-zinc-200 rounded-md bg-white ring-1 ring-zinc-200 dark:divide-zinc-700 dark:bg-zinc-800 dark:ring-zinc-700">
              {list.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  selected={item.id === selectedItem}
                  editable={editable}
                  onSelect={() => selectItem(item.id === selectedItem ? null : item.id)}
                  run={run}
                  jobId={job.id}
                />
              ))}
            </ul>
          </Section>
        );
      })}

      {editable && (
        <Section title={t("analysis.add")}>
          {itemDraft ? (
            <Alert>
              <div className="flex items-center justify-between gap-2">
                <span>
                  {t(`analysis.draw_hint.${itemDraft.mode}`, {
                    category: t(categoryLabelKey({ kind: itemDraft.kind, category: itemDraft.category })),
                  })}
                </span>
                <Button variant="ghost" onClick={() => startItemDraft(null)}>
                  {t("common.cancel")}
                </Button>
              </div>
            </Alert>
          ) : (
            <div className="flex gap-2">
              <select
                className={inputClass}
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                data-testid="new-item-category"
              >
                <optgroup label={t("analysis.objects")}>
                  {OBJECT_CATEGORIES.map((c) => (
                    <option key={c} value={`object:${c}`}>
                      {t(`category.${c}`)}
                    </option>
                  ))}
                </optgroup>
                <optgroup label={t("analysis.surfaces")}>
                  {SURFACE_TYPES.map((c) => (
                    <option key={c} value={`surface:${c}`}>
                      {t(`surface.${c}`)}
                    </option>
                  ))}
                </optgroup>
              </select>
              <Button
                onClick={() => {
                  const [kind, category] = newCategory.split(":") as ["object" | "surface", never];
                  selectItem(null);
                  startItemDraft(draftFor(kind, category));
                }}
                data-testid="new-item-draw"
              >
                {t("analysis.draw")}
              </Button>
            </div>
          )}
        </Section>
      )}
    </>
  );
}

function ItemRow({
  item,
  selected,
  editable,
  onSelect,
  run,
  jobId,
}: {
  item: SceneItem;
  selected: boolean;
  editable: boolean;
  onSelect: () => void;
  run: (fn: () => Promise<JobView>) => Promise<void>;
  jobId: string;
}) {
  const { t } = useT();
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  const patch = (p: ItemPatch) => run(() => api.updateItem(jobId, item.id, p));
  // Shown immediately; the server copy follows a moment later (or reverts it on error).
  const [includedNow, setIncludedNow] = useState<boolean | null>(null);
  useEffect(() => setIncludedNow(null), [item]);
  const included = includedNow ?? item.included ?? true;
  const height = item.attrs?.height_m;

  return (
    <li ref={ref} className={selected ? "bg-accent-50 dark:bg-accent-700/20" : ""} data-testid={`item-${item.id}`}>
      <div className="flex items-center gap-2 px-2 py-1.5">
        <input
          type="checkbox"
          title={t("analysis.include")}
          checked={included}
          disabled={!editable}
          onChange={(e) => {
            setIncludedNow(e.target.checked);
            void patch({ included: e.target.checked }).finally(() => setIncludedNow(null));
          }}
          data-testid={`include-${item.id}`}
        />
        <button className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm" onClick={onSelect}>
          <span
            className="h-3 w-3 shrink-0 rounded-sm ring-1 ring-black/20"
            style={{ background: CATEGORY_COLORS[item.category] }}
          />
          <span className={`truncate ${included ? "" : "text-zinc-400 line-through"}`}>{itemTitle(t, item)}</span>
          {item.kind === "object" && height != null && (
            <span className="shrink-0 text-xs text-zinc-500">{height} m</span>
          )}
          <span className="ml-auto shrink-0 text-[10px] uppercase text-zinc-400">
            {item.edited ? t("analysis.badge_edited") : item.ai ? t("analysis.badge_ai") : t(`analysis.badge_${item.source}`)}
          </span>
        </button>
      </div>
      {selected && <ItemEditor item={item} editable={editable} patch={patch} remove={() => run(() => api.deleteItem(jobId, item.id))} />}
    </li>
  );
}

function ItemEditor({
  item,
  editable,
  patch,
  remove,
}: {
  item: SceneItem;
  editable: boolean;
  patch: (p: ItemPatch) => Promise<void>;
  remove: () => Promise<void>;
}) {
  const { t } = useT();
  const [height, setHeight] = useState(item.attrs?.height_m?.toString() ?? "");
  const [name, setName] = useState(item.name ?? "");
  useEffect(() => {
    setHeight(item.attrs?.height_m?.toString() ?? "");
    setName(item.name ?? "");
  }, [item]);
  const categories = item.kind === "object" ? OBJECT_CATEGORIES : SURFACE_TYPES;
  const attrs = item.attrs ?? {};
  const facts = [
    attrs.levels != null && t("attr.levels", { value: attrs.levels }),
    attrs.roof_shape && t("attr.roof_shape", { value: attrs.roof_shape }),
    attrs.material && t("attr.material", { value: attrs.material }),
    attrs.width_m != null && t("attr.width", { value: attrs.width_m }),
    attrs.covered != null && t(attrs.covered ? "attr.covered" : "attr.open"),
  ].filter(Boolean);
  const colors = [attrs.roof_color, attrs.wall_color, attrs.seat_color].filter(Boolean) as string[];

  return (
    <div className="space-y-2 border-t border-zinc-200 px-3 py-2 text-sm dark:border-zinc-700">
      {item.ai && (
        <p className="text-xs text-zinc-600 dark:text-zinc-300" data-testid="ai-assessment">
          🤖 {item.ai.summary}{" "}
          <span className="text-zinc-400">({t("analysis.confidence", { value: Math.round(item.ai.confidence * 100) })})</span>
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-0.5">
          <span className="text-xs text-zinc-500">{t("analysis.category")}</span>
          <select
            className={inputClass}
            value={item.category}
            disabled={!editable}
            onChange={(e) => patch({ category: e.target.value as SceneItem["category"] })}
            data-testid="edit-category"
          >
            {categories.map((c) => (
              <option key={c} value={c}>
                {t(categoryLabelKey({ kind: item.kind, category: c }))}
              </option>
            ))}
          </select>
        </label>
        {item.kind === "object" && (
          <label className="space-y-0.5">
            <span className="text-xs text-zinc-500">{t("analysis.height")}</span>
            <input
              type="number"
              min={0}
              max={300}
              step={0.5}
              className={inputClass}
              value={height}
              disabled={!editable}
              onChange={(e) => setHeight(e.target.value)}
              onBlur={() => {
                const value = height === "" ? null : Number(height);
                if (value !== (item.attrs?.height_m ?? null)) void patch({ attrs: { height_m: value } });
              }}
              data-testid="edit-height"
            />
          </label>
        )}
      </div>
      <label className="block space-y-0.5">
        <span className="text-xs text-zinc-500">{t("analysis.name")}</span>
        <input
          className={inputClass}
          value={name}
          disabled={!editable}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => {
            if (name !== (item.name ?? "")) void patch({ name: name || null });
          }}
        />
      </label>
      {(facts.length > 0 || colors.length > 0) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
          {facts.map((f, i) => (
            <span key={i}>{f}</span>
          ))}
          {colors.map((c) => (
            <span key={c} title={c} className="inline-block h-3 w-3 rounded-sm ring-1 ring-black/20" style={{ background: c }} />
          ))}
        </div>
      )}
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span>{item.osm_ref ? `OSM ${item.osm_ref}` : t("analysis.badge_user")}</span>
        {editable && (
          <Button variant="ghost" onClick={remove} data-testid="delete-item">
            🗑 {t("analysis.delete")}
          </Button>
        )}
      </div>
    </div>
  );
}
