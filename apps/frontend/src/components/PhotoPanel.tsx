import { useEffect, useState } from "react";
import { api, ApiError, authedImageUrl, type SceneItem } from "../api/client";
import type { components } from "../api/schema";
import { useT } from "../i18n";
import { useApp } from "../store";
import { Alert, Button, Checkbox, Spinner } from "./ui";

type Candidate = components["schemas"]["PhotoCandidate"];
const MAX_PHOTOS = 8;
const SOURCE_NAMES: Record<string, string> = {
  wikimedia: "Wikimedia Commons",
  panoramax: "Panoramax",
  mapillary: "Mapillary",
};

/** Image behind the backend's auth, shown as soon as it has loaded. */
function AuthedImg({ path, alt, className }: { path: string; alt: string; className: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let revoked = false;
    let current: string | null = null;
    authedImageUrl(path)
      .then((u) => {
        if (revoked) URL.revokeObjectURL(u);
        else setUrl((current = u));
      })
      .catch(() => {});
    return () => {
      revoked = true;
      if (current) URL.revokeObjectURL(current);
    };
  }, [path]);
  return url ? <img src={url} alt={alt} className={className} /> : <div className={`${className} animate-pulse bg-zinc-200 dark:bg-zinc-700`} />;
}

function credit(p: { source: string; author?: string | null; license?: string | null }) {
  return [SOURCE_NAMES[p.source], p.author, p.license].filter(Boolean).join(" · ");
}

/** Photos of the real object for the AI architect: own uploads or open sources nearby. */
export function PhotoPanel({ jobId, item }: { jobId: string; item: SceneItem }) {
  const { t } = useT();
  const setJob = useApp((s) => s.setJob);
  const hasMapillary = useApp((s) => Boolean(s.system?.has_mapillary_token));
  const [rights, setRights] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchAsked, setSearchAsked] = useState(false);
  const [results, setResults] = useState<{ candidates: Candidate[]; errors: Record<string, string> } | null>(null);
  const [imported, setImported] = useState<Set<string>>(new Set());
  const photos = item.photos ?? [];
  const full = photos.length >= MAX_PHOTOS;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    } finally {
      setBusy(false);
    }
  };

  const upload = (files: FileList | null) =>
    run(async () => {
      for (const file of Array.from(files ?? []).slice(0, MAX_PHOTOS - photos.length)) {
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(file);
        });
        setJob(await api.addPhoto(jobId, item.id, data));
      }
    });
  const search = () =>
    run(async () => {
      setResults(await api.searchPhotos(jobId, item.id));
    });
  const take = (c: Candidate) =>
    run(async () => {
      setJob(await api.importPhoto(jobId, c.id));
      setImported((s) => new Set(s).add(c.id));
    });

  return (
    <div className="space-y-1.5" data-testid="photo-panel">
      <div className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
        📷 {t("photos.title", { count: photos.length, max: MAX_PHOTOS })}
      </div>
      {photos.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="photo-list">
          {photos.map((p) => (
            <div key={p.id} className="group relative" title={p.source === "user" ? t("photos.own") : credit(p)}>
              <AuthedImg path={`/api/jobs/${jobId}/photos/${p.id}`} alt="" className="h-14 w-20 rounded object-cover" />
              <button
                className="absolute top-0.5 right-0.5 hidden h-5 w-5 rounded-full bg-black/70 text-xs text-white group-hover:block"
                onClick={() => run(async () => setJob(await api.deletePhoto(jobId, p.id)))}
                aria-label={t("common.remove")}
                disabled={busy}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      <p className="text-xs text-zinc-500">{t("photos.hint")}</p>

      <Checkbox label={<span className="text-xs">{t("photos.rights")}</span>} checked={rights} onChange={setRights} />
      <label
        className={`inline-flex cursor-pointer items-center gap-1 rounded-md border border-zinc-300 px-2.5 py-1 text-xs dark:border-zinc-600 ${
          !rights || busy || full ? "pointer-events-none opacity-50" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
        }`}
      >
        ＋ {t("photos.add")}
        <input
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          disabled={!rights || busy || full}
          onChange={(e) => {
            upload(e.target.files);
            e.target.value = "";
          }}
          data-testid="photo-upload"
        />
      </label>

      <div className="space-y-1.5 border-t border-dashed border-zinc-200 pt-1.5 dark:border-zinc-700">
        {!searchAsked ? (
          <Button onClick={() => setSearchAsked(true)} disabled={busy || full} data-testid="photo-search">
            🔎 {t("photos.search")}
          </Button>
        ) : !results ? (
          <div className="space-y-1.5">
            <p className="text-xs text-zinc-600 dark:text-zinc-300">
              {t(hasMapillary ? "photos.search_info_mapillary" : "photos.search_info")}
            </p>
            <div className="flex gap-2">
              <Button variant="primary" onClick={search} disabled={busy} data-testid="photo-search-go">
                {t("photos.search_go")}
              </Button>
              <Button variant="ghost" onClick={() => setSearchAsked(false)} disabled={busy}>
                {t("common.cancel")}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-1.5" data-testid="photo-results">
            {Object.entries(results.errors).map(([src, msg]) => (
              <p key={src} className="text-xs text-amber-600">
                {t("photos.source_failed", { source: SOURCE_NAMES[src] ?? src, detail: msg })}
              </p>
            ))}
            {results.candidates.length === 0 ? (
              <p className="text-xs text-zinc-500">{t("photos.none_found")}</p>
            ) : (
              <>
                <p className="text-xs text-zinc-500">{t("photos.pick")}</p>
                <div className="grid max-h-72 grid-cols-2 gap-1.5 overflow-y-auto">
                  {results.candidates.map((c) => (
                    <button
                      key={c.id}
                      className="relative text-left disabled:opacity-50"
                      onClick={() => take(c)}
                      disabled={busy || full || imported.has(c.id)}
                      title={[c.title, credit(c)].filter(Boolean).join("\n")}
                      data-testid="photo-candidate"
                    >
                      <AuthedImg
                        path={`/api/jobs/${jobId}/photo-search/${c.id}/thumb`}
                        alt={c.title ?? ""}
                        className="h-20 w-full rounded object-cover"
                      />
                      <span className="block truncate text-[10px] text-zinc-500">
                        {imported.has(c.id) ? "✓ " : ""}
                        {SOURCE_NAMES[c.source]}
                        {c.distance_m != null ? ` · ${Math.round(c.distance_m)} m` : ""}
                      </span>
                    </button>
                  ))}
                </div>
              </>
            )}
            <Button variant="ghost" onClick={() => (setResults(null), setSearchAsked(false))} disabled={busy}>
              {t("common.close")}
            </Button>
          </div>
        )}
      </div>
      {busy && (
        <div className="flex items-center gap-2 text-xs">
          <Spinner /> {t("photos.working")}
        </div>
      )}
      {error && <Alert kind="error">{error}</Alert>}
    </div>
  );
}
