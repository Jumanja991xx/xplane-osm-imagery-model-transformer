import { useEffect, useState } from "react";
import { api, ApiError, type JobSummary } from "../../api/client";
import { MAX_AOI_AREA_KM2, ringAreaKm2 } from "../../geo";
import { useT } from "../../i18n";
import { useApp } from "../../store";
import { Alert, Button, Spinner } from "../ui";

export function AreaStep() {
  const { t } = useT();
  const { aoi, drawMode, job, setDrawMode, requestClear, setJob, reset } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<JobSummary[]>([]);
  useEffect(() => {
    if (!job) void api.listJobs().then(setRecent).catch(() => setRecent([]));
  }, [job]);
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
          <h3 className="text-xs font-semibold text-zinc-500 uppercase">{t("area.recent")}</h3>
          <ul className="divide-y divide-zinc-200 rounded-md bg-white ring-1 ring-zinc-200 dark:divide-zinc-700 dark:bg-zinc-800 dark:ring-zinc-700">
            {recent.slice(0, 6).map((r) => (
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
