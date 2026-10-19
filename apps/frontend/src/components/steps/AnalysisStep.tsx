import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type ItemPatch,
  type JobView,
  type Question,
  type SceneItem,
  type SimilarEstimate,
} from "../../api/client";
import { useT, type Translate } from "../../i18n";
import { CATEGORY_COLORS, categoryLabelKey, draftFor, OBJECT_CATEGORIES, SURFACE_TYPES } from "../../scene";
import { useApp } from "../../store";
import { ProgressList } from "../ProgressList";
import { Alert, Button, Checkbox, inputClass, Section, Spinner } from "../ui";

const WARNING_NOTES = new Set([
  "analysis.dropped_category",
  "imagery.failed",
  "imagery.not_covered",
  "imagery.custom_missing",
  "analysis.ai_limit_reached",
  "analysis.ai_auth_failed",
  "analysis.ai_call_failed",
  "analysis.ai_fallback_model",
  "analysis.nothing_found",
]);

/** Scrolls only the sidebar list, never the whole window (unlike scrollIntoView). */
function scrollIntoList(el: HTMLElement) {
  let box = el.parentElement;
  while (box && getComputedStyle(box).overflowY !== "auto") box = box.parentElement;
  if (!box) return;
  const r = el.getBoundingClientRect();
  const b = box.getBoundingClientRect();
  if (r.top < b.top) box.scrollBy({ top: r.top - b.top - 8, behavior: "smooth" });
  else if (r.bottom > b.bottom) box.scrollBy({ top: r.bottom - b.bottom + 8, behavior: "smooth" });
}

/** Small amounts matter here (Haiku: fractions of a cent), so never show "0.00". */
export function formatUsd(usd: number): string {
  if (usd === 0) return "0";
  return usd < 0.01 ? usd.toFixed(4) : usd.toFixed(2);
}

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
        <div className="sticky -bottom-4 z-10 -mx-4 flex justify-end gap-2 border-t border-zinc-200 bg-zinc-50/95 px-4 py-2 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/95">
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
            cost: formatUsd(ai.cost_usd ?? 0),
          })}
          {(ai.tiles_searched ?? 0) > 0 && (
            <div>{t("analysis.ai_detect_summary", { tiles: ai.tiles_searched, proposals: ai.proposals ?? 0 })}</div>
          )}
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

  const proposals = items.filter((i) => i.proposal);
  const jobId = job.id;
  const [marked, setMarked] = useState<Set<string>>(new Set());
  const [bulkCategory, setBulkCategory] = useState<string>("");
  const accept = (ids: string[], category?: string) =>
    run(async () => {
      let updated: JobView | null = null;
      for (const id of ids) {
        const item = items.find((i) => i.id === id);
        const fits = category && item && (item.kind === "object" ? OBJECT_CATEGORIES : SURFACE_TYPES).includes(
          category as never,
        );
        updated = await api.updateItem(jobId, id, {
          proposal: false,
          ...(fits ? { category: category as SceneItem["category"] } : {}),
        });
      }
      setMarked(new Set());
      return updated!;
    });
  const [multiEdit, setMultiEdit] = useState(false);
  const fitsKind = (item: SceneItem, category: string) =>
    (item.kind === "object" ? OBJECT_CATEGORIES : SURFACE_TYPES).includes(category as never);
  const bulkPatch = (make: (item: SceneItem) => ItemPatch | null) =>
    run(async () => {
      let updated: JobView = job;
      for (const id of marked) {
        const item = items.find((i) => i.id === id);
        const p = item && make(item);
        if (p) updated = await api.updateItem(jobId, id, p);
      }
      setMarked(new Set());
      return updated;
    });
  const toggleMarked = (id: string) =>
    setMarked((m) => {
      const next = new Set(m);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <>
      {proposals.length > 0 && (
        <Section title={`${t("analysis.proposals")} (${proposals.length})`}>
          <p className="text-xs text-zinc-500">{t("analysis.proposals_hint")}</p>
          {editable && proposals.length > 1 && (
            <Button onClick={() => accept(proposals.map((p) => p.id))} data-testid="accept-all-proposals">
              ✓ {t("analysis.accept_all", { count: proposals.length })}
            </Button>
          )}
          {editable && marked.size > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-md bg-amber-50 p-2 text-sm dark:bg-amber-500/10">
              <span>{t("analysis.marked", { count: marked.size })}</span>
              <select
                className={`${inputClass} w-auto`}
                value={bulkCategory}
                onChange={(e) => setBulkCategory(e.target.value)}
                data-testid="bulk-category"
              >
                <option value="">{t("analysis.keep_category")}</option>
                {OBJECT_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {t(`category.${c}`)}
                  </option>
                ))}
              </select>
              <Button onClick={() => accept([...marked], bulkCategory || undefined)} data-testid="bulk-accept">
                ✓ {t("analysis.accept")}
              </Button>
              <Button
                variant="ghost"
                onClick={() =>
                  run(async () => {
                    let updated: JobView | null = null;
                    for (const id of marked) updated = await api.deleteItem(jobId, id);
                    setMarked(new Set());
                    return updated!;
                  })
                }
              >
                ✗ {t("analysis.reject")}
              </Button>
            </div>
          )}
          <ul className="divide-y divide-zinc-200 rounded-md bg-white ring-1 ring-amber-300 dark:divide-zinc-700 dark:bg-zinc-800 dark:ring-amber-500/50">
            {proposals.map((item) => (
              <li
                key={item.id}
                className={item.id === selectedItem ? "bg-accent-50 dark:bg-accent-700/20" : ""}
                data-testid={`proposal-${item.id}`}
              >
                <div className="flex items-center gap-2 px-2 py-1.5">
                  {editable && (
                    <input
                      type="checkbox"
                      title={t("analysis.mark")}
                      checked={marked.has(item.id)}
                      onChange={() => toggleMarked(item.id)}
                      data-testid={`mark-${item.id}`}
                    />
                  )}
                  <button
                    className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
                    onClick={() => selectItem(item.id === selectedItem ? null : item.id)}
                  >
                    <span
                      className="h-3 w-3 shrink-0 rounded-sm border-2 border-dashed"
                      style={{ borderColor: CATEGORY_COLORS[item.category] }}
                    />
                    <span className="truncate">{itemTitle(t, item)}</span>
                    {item.attrs?.height_m != null && (
                      <span className="shrink-0 text-xs text-zinc-500">{item.attrs.height_m} m</span>
                    )}
                    {item.ai && (
                      <span className="shrink-0 text-xs text-zinc-400">{Math.round(item.ai.confidence * 100)} %</span>
                    )}
                  </button>
                  {editable && (
                    <>
                      <Button
                        variant="ghost"
                        title={t("analysis.accept")}
                        onClick={() => accept([item.id])}
                        data-testid={`accept-${item.id}`}
                      >
                        ✓
                      </Button>
                      <Button
                        variant="ghost"
                        title={t("analysis.reject")}
                        onClick={() => run(() => api.deleteItem(jobId, item.id))}
                        data-testid={`reject-${item.id}`}
                      >
                        ✗
                      </Button>
                    </>
                  )}
                </div>
                {item.id === selectedItem && (
                  <ItemEditor
                    item={item}
                    editable={editable}
                    patch={(p) => run(() => api.updateItem(jobId, item.id, p))}
                    remove={() => run(() => api.deleteItem(jobId, item.id))}
                  />
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {editable && (
        <div className="flex justify-end">
          <Button
            variant="ghost"
            onClick={() => {
              setMultiEdit(!multiEdit);
              setMarked(new Set());
            }}
            data-testid="multi-edit"
          >
            {multiEdit ? t("analysis.multi_done") : `☑ ${t("analysis.multi_edit")}`}
          </Button>
        </div>
      )}
      {multiEdit && marked.size > 0 && (
        <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md bg-amber-50 p-2 text-sm shadow dark:bg-amber-900/60">
          <span>{t("analysis.marked", { count: marked.size })}</span>
          <select
            className={`${inputClass} w-auto`}
            value={bulkCategory}
            onChange={(e) => setBulkCategory(e.target.value)}
            data-testid="multi-category"
          >
            <option value="">{t("analysis.choose_category")}</option>
            <optgroup label={t("analysis.objects")}>
              {OBJECT_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {t(`category.${c}`)}
                </option>
              ))}
            </optgroup>
            <optgroup label={t("analysis.surfaces")}>
              {SURFACE_TYPES.map((c) => (
                <option key={c} value={c}>
                  {t(`surface.${c}`)}
                </option>
              ))}
            </optgroup>
          </select>
          <Button
            disabled={!bulkCategory}
            onClick={() => bulkPatch((item) => (fitsKind(item, bulkCategory) ? { category: bulkCategory as SceneItem["category"] } : null))}
            data-testid="multi-apply"
          >
            {t("analysis.change")}
          </Button>
          <Button variant="ghost" onClick={() => bulkPatch(() => ({ included: false }))}>
            {t("analysis.exclude")}
          </Button>
          <Button variant="ghost" onClick={() => bulkPatch(() => ({ included: true }))}>
            {t("analysis.include_short")}
          </Button>
        </div>
      )}

      {groups.map(({ kind, title }) => {
        const list = items.filter((i) => i.kind === kind && !i.proposal);
        const allMarked = list.length > 0 && list.every((i) => marked.has(i.id));
        return (
          <Section key={kind} title={`${title} (${list.length})`}>
            {!list.length && <p className="text-sm">{t("analysis.none")}</p>}
            {multiEdit && list.length > 0 && (
              <Checkbox
                label={t("analysis.mark_all")}
                checked={allMarked}
                onChange={(on) =>
                  setMarked((m) => {
                    const next = new Set(m);
                    for (const i of list) {
                      if (on) next.add(i.id);
                      else next.delete(i.id);
                    }
                    return next;
                  })
                }
              />
            )}
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
                  marked={multiEdit ? marked.has(item.id) : null}
                  onMark={() => toggleMarked(item.id)}
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
  marked,
  onMark,
}: {
  item: SceneItem;
  selected: boolean;
  editable: boolean;
  onSelect: () => void;
  run: (fn: () => Promise<JobView>) => Promise<void>;
  jobId: string;
  marked: boolean | null; // null: not in multi-edit mode
  onMark: () => void;
}) {
  const { t } = useT();
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (selected && ref.current) scrollIntoList(ref.current);
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
        {marked !== null && (
          <input
            type="checkbox"
            className="accent-amber-500"
            title={t("analysis.mark")}
            checked={marked}
            onChange={onMark}
            data-testid={`mark-${item.id}`}
          />
        )}
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
            {item.edited
              ? t("analysis.badge_edited")
              : item.rating
                ? t("analysis.badge_rating")
                : item.remembered
                  ? t("analysis.badge_memory")
                  : item.source === "ai"
                    ? t("analysis.badge_found")
                : item.ai
                  ? t("analysis.badge_ai")
                  : t(`analysis.badge_${item.source}`)}
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
      {editable && item.kind === "object" && <FindSimilar item={item} />}
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span>
          {item.osm_ref
            ? `OSM ${item.osm_ref}`
            : item.source === "ai"
              ? t("analysis.badge_found")
              : t("analysis.badge_user")}
        </span>
        {editable && (
          <Button variant="ghost" onClick={remove} data-testid="delete-item">
            🗑 {t("analysis.delete")}
          </Button>
        )}
      </div>
    </div>
  );
}

/** "Find similar": shows what it costs and what is sent, then runs only on a yes. */
function FindSimilar({ item }: { item: SceneItem }) {
  const { t } = useT();
  const { job, setJob } = useApp();
  const [estimate, setEstimate] = useState<SimilarEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!job?.brief?.use_ai || !job.analysis?.imagery) return null;

  const fail = (e: unknown) => setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
  const ask = async () => {
    setError(null);
    setBusy(true);
    try {
      setEstimate(await api.similarEstimate(job.id, item.id));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  const confirm = async () => {
    if (!estimate) return;
    setBusy(true);
    setError(null);
    try {
      const before = (job.analysis?.items ?? []).filter((i) => i.proposal).length;
      const updated = await api.findSimilar(job.id, item.id, Math.min(estimate.estimate_usd, estimate.budget_usd));
      setJob(updated);
      const after = (updated.analysis?.items ?? []).filter((i) => i.proposal).length;
      setEstimate(null);
      setError(t("analysis.similar_done", { count: Math.max(0, after - before) }));
    } catch (e) {
      fail(e);
      setEstimate(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5">
      {!estimate && (
        <Button onClick={ask} disabled={busy} data-testid="find-similar">
          {busy && <Spinner />}🔍 {t("analysis.find_similar")}
        </Button>
      )}
      {estimate && (
        <div className="space-y-1.5 rounded-md bg-amber-50 p-2 text-xs dark:bg-amber-500/10" data-testid="similar-confirm">
          <p>
            {t("analysis.similar_question", {
              tiles: estimate.tiles,
              model: t(`model.${estimate.model}`),
              cost: formatUsd(Math.min(estimate.estimate_usd, estimate.budget_usd)),
            })}
          </p>
          {estimate.budget_usd < estimate.estimate_usd && <p>{t("analysis.similar_budget")}</p>}
          <div className="flex gap-2">
            <Button variant="primary" onClick={confirm} disabled={busy || estimate.budget_usd <= 0} data-testid="similar-go">
              {busy && <Spinner />}
              {busy ? t("analysis.similar_running") : t("analysis.similar_go")}
            </Button>
            <Button variant="ghost" onClick={() => setEstimate(null)} disabled={busy}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      {error && <p className="text-xs text-zinc-600 dark:text-zinc-300">{error}</p>}
    </div>
  );
}
