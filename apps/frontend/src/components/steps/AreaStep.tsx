import { useEffect, useState } from "react";
import { api, ApiError, type JobSummary } from "../../api/client";
import { MAX_AOI_AREA_KM2, ringAreaKm2 } from "../../geo";
import { useT } from "../../i18n";
import { useApp } from "../../store";
import { Alert, Button, Spinner } from "../ui";

const RECENT_SHOWN = 6;

export function AreaStep() {
  const { t } = useT();
  const { aoi, drawMode, job, setDrawMode, requestClear, setJob, reset } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<JobSummary[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    if (!job)
      void api
        .listJobs()
        .then((jobs) => setRecent([...jobs].sort((a, b) => b.updated_at.localeCompare(a.updated_at))))
        .catch(() => setRecent([]));
  }, [job]);
  const needle = filter.trim().toLowerCase();
  const shown = showAll
    ? recent.filter((r) => !needle || (r.package_name ?? "").toLowerCase().includes(needle))
    : recent.slice(0, RECENT_SHOWN);
  const open = async (id: string) => {
    const opened = await api.getJob(id);
    setJob(opened);
  };

  const geometry = job?.aoi ?? aoi;
  const area = geometry ? ringAreaKm2(geometry.coordinates[0]) : 0;
  const tooLarge = area > MAX_AOI_AREA_KM2;

  const next = async () => {
    if (!aoi) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await api.createJob(aoi));
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (job) {
    return (
      <div className="space-y-3">
        <Alert>{t("area.locked")}</Alert>
        <p className="text-sm">{t("area.size", { km2: area.toFixed(3) })}</p>
        <Button onClick={reset}>{t("common.new_job")}</Button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-zinc-600 dark:text-zinc-300">{t("area.intro")}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          variant={drawMode === "polygon" ? "primary" : "secondary"}
          onClick={() => setDrawMode("polygon")}
          data-testid="draw-polygon"
        >
          {t("area.draw_polygon")}
        </Button>
        <Button
          variant={drawMode === "rectangle" ? "primary" : "secondary"}
          onClick={() => setDrawMode("rectangle")}
          data-testid="draw-rectangle"
        >
          {t("area.draw_rectangle")}
        </Button>
        <Button variant="ghost" onClick={requestClear} disabled={!aoi && drawMode === "none"}>
          {t("area.clear")}
        </Button>
      </div>
      {drawMode !== "none" && (
        <p className="text-xs text-zinc-500">
          {t(drawMode === "polygon" ? "area.hint_polygon" : "area.hint_rectangle")}
        </p>
      )}
      {aoi ? (
        <p className="text-sm" data-testid="area-size">
          {t("area.size", { km2: area.toFixed(3) })}
        </p>
      ) : (
        <p className="text-sm text-zinc-500">{t("area.none")}</p>
      )}
      {tooLarge && <Alert kind="warning">{t("area.too_large", { max: MAX_AOI_AREA_KM2 })}</Alert>}
      {recent.length > 0 && !aoi && (
        <div className="space-y-1 pt-2" data-testid="recent-jobs">
          <h3 className="text-xs font-semibold text-zinc-500 uppercase">
            {t(showAll ? "area.all_jobs" : "area.recent", { count: recent.length })}
          </h3>
          {showAll && recent.length > RECENT_SHOWN && (
            <input
              className="w-full rounded-md bg-white px-2 py-1 text-sm ring-1 ring-zinc-200 outline-none dark:bg-zinc-800 dark:ring-zinc-700"
              value={filter}
              placeholder={t("area.filter_jobs")}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="recent-filter"
            />
          )}
          <ul
            className={`${showAll ? "max-h-80 overflow-y-auto " : ""}divide-y divide-zinc-200 rounded-md bg-white ring-1 ring-zinc-200 dark:divide-zinc-700 dark:bg-zinc-800 dark:ring-zinc-700`}
          >
            {shown.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2 px-2 py-1.5 text-sm">
                <span className="min-w-0">
                  <span className="block truncate">{r.package_name ?? t("area.unnamed")}</span>
                  <span className="text-xs text-zinc-500">
                    {new Date(r.updated_at).toLocaleString()} · {t(`state.${r.state}`)} · {r.objects}{" "}
                    {t("analysis.objects")}
                  </span>
                </span>
                <Button onClick={() => open(r.id)} data-testid={`open-job-${r.id}`}>
                  {t("area.open")}
                </Button>
              </li>
            ))}
          </ul>
          {recent.length > RECENT_SHOWN && (
            <button
              type="button"
              className="text-xs text-accent-600 hover:underline"
              onClick={() => {
                setShowAll(!showAll);
                setFilter("");
              }}
              data-testid="recent-toggle"
            >
              {showAll ? t("area.show_fewer") : t("area.show_all", { count: recent.length })}
            </button>
          )}
        </div>
      )}
      {error && <Alert kind="error">{error}</Alert>}
      <div className="flex justify-end">
        <Button variant="primary" onClick={next} disabled={!aoi || tooLarge || busy} data-testid="area-next">
          {busy && <Spinner />}
          {t("common.next")}
        </Button>
      </div>
    </div>
  );
}
