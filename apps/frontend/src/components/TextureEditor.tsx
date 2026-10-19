import { useEffect, useRef, useState } from "react";
import { api, ApiError, authedImageUrl, texturePreviewUrl, type PhotoTexture, type SceneItem } from "../api/client";
import { useT } from "../i18n";
import { useApp } from "../store";
import { Alert, Button, Checkbox, inputClass, Spinner } from "./ui";

const CORNER_LABELS = ["↖", "↗", "↘", "↙"];
const DEFAULT_CORNERS = [
  [0.2, 0.2],
  [0.8, 0.2],
  [0.8, 0.8],
  [0.2, 0.8],
];

function nextName(taken: string[]): string {
  for (const base of ["front", "side", "back", "wall"]) {
    if (!taken.includes(base)) return base;
  }
  let n = 2;
  while (taken.includes(`wall${n}`)) n++;
  return `wall${n}`;
}

/** Mark a facade on a photo by its four corners: it is straightened and painted onto
 * the model as a texture (only photos whose licence allows sharing). */
export function TextureEditor({
  jobId,
  item,
  photoId,
  texture,
  onClose,
}: {
  jobId: string;
  item: SceneItem;
  photoId: string;
  texture?: PhotoTexture; // edit an existing one
  onClose: () => void;
}) {
  const { t } = useT();
  const setJob = useApp((s) => s.setJob);
  const textures = item.textures ?? [];
  const taken = textures.filter((x) => x.name !== texture?.name).map((x) => x.name);
  const [name, setName] = useState(texture?.name ?? nextName(taken));
  const [corners, setCorners] = useState<number[][]>(texture?.corners ?? DEFAULT_CORNERS);
  const [width, setWidth] = useState(String(texture?.width_m ?? ""));
  const [height, setHeight] = useState(String(texture?.height_m ?? item.attrs?.height_m ?? ""));
  const [repeat, setRepeat] = useState(texture?.repeat ?? false);
  const [walls, setWalls] = useState(texture?.walls ?? false);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<number | null>(null);

  useEffect(() => {
    let url: string | null = null;
    authedImageUrl(`/api/jobs/${jobId}/photos/${photoId}`)
      .then((u) => setPhotoUrl((url = u)))
      .catch(() => setError(t("error.photo_not_found")));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [jobId, photoId, t]);
  useEffect(() => () => void (result && URL.revokeObjectURL(result)), [result]);

  const spec = (): PhotoTexture | null => {
    const w = Number(width.replace(",", "."));
    const h = Number(height.replace(",", "."));
    if (!/^[a-z][a-z0-9_]{0,23}$/.test(name) || !(w >= 0.5) || !(h >= 0.5)) return null;
    return { name, photo_id: photoId, corners, width_m: w, height_m: h, repeat, walls, source: "user" };
  };
  const valid = spec() !== null && !taken.includes(name);

  const move = (e: React.PointerEvent) => {
    if (drag.current === null || !box.current) return;
    const r = box.current.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    setCorners((c) => c.map((p, i) => (i === drag.current ? [x, y] : p)));
    setResult(null);
  };

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
  const showResult = () =>
    run(async () => {
      const s = spec();
      if (s) setResult(await texturePreviewUrl(jobId, item.id, s));
    });
  const save = () =>
    run(async () => {
      const s = spec();
      if (!s) return;
      const others = textures.filter((x) => x.name !== texture?.name);
      setJob(await api.setTextures(jobId, item.id, [...others, s]));
      onClose();
    });

  const pts = corners.map(([x, y]) => `${x * 100},${y * 100}`).join(" ");
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" data-testid="texture-editor">
      <div className="flex max-h-full w-full max-w-4xl flex-col gap-3 overflow-y-auto rounded-lg bg-white p-4 shadow-xl dark:bg-zinc-900">
        <h2 className="text-base font-semibold">🧱 {t("texture.title")}</h2>
        <p className="text-xs text-zinc-600 dark:text-zinc-300">{t("texture.intro")}</p>
        <div className="grid gap-3 md:grid-cols-[3fr_2fr]">
          <div
            ref={box}
            className="relative touch-none select-none"
            onPointerMove={move}
            onPointerUp={() => (drag.current = null)}
            onPointerLeave={() => (drag.current = null)}
          >
            {photoUrl ? <img src={photoUrl} alt="" className="block w-full rounded" draggable={false} /> : <div className="h-64 animate-pulse rounded bg-zinc-200 dark:bg-zinc-700" />}
            <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
              <polygon points={pts} fill="rgba(59,130,246,0.18)" stroke="#3b82f6" strokeWidth="0.4" vectorEffect="non-scaling-stroke" />
            </svg>
            {corners.map(([x, y], i) => (
              <button
                key={i}
                className="absolute flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 cursor-grab items-center justify-center rounded-full border-2 border-white bg-blue-600 text-[10px] text-white shadow"
                style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
                onPointerDown={(e) => {
                  (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
                  drag.current = i;
                }}
                onPointerMove={move}
                onPointerUp={() => (drag.current = null)}
                aria-label={t("texture.corner", { n: i + 1 })}
                data-testid={`texture-corner-${i}`}
              >
                {CORNER_LABELS[i]}
              </button>
            ))}
          </div>
          <div className="space-y-2 text-sm">
            <p className="text-xs text-zinc-500">{t("texture.corners_hint")}</p>
            <label className="block">
              <span className="text-xs text-zinc-600 dark:text-zinc-300">{t("texture.name")}</span>
              <input className={inputClass} value={name} onChange={(e) => setName(e.target.value.toLowerCase())} data-testid="texture-name" />
            </label>
            {taken.includes(name) && <p className="text-xs text-red-600">{t("texture.name_taken")}</p>}
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-xs text-zinc-600 dark:text-zinc-300">{t("texture.width")}</span>
                <input className={inputClass} inputMode="decimal" value={width} onChange={(e) => setWidth(e.target.value)} data-testid="texture-width" />
              </label>
              <label className="block">
                <span className="text-xs text-zinc-600 dark:text-zinc-300">{t("texture.height")}</span>
                <input className={inputClass} inputMode="decimal" value={height} onChange={(e) => setHeight(e.target.value)} data-testid="texture-height" />
              </label>
            </div>
            <Checkbox label={<span className="text-xs">{t("texture.repeat")}</span>} checked={repeat} onChange={setRepeat} />
            <Checkbox label={<span className="text-xs">{t("texture.walls")}</span>} checked={walls} onChange={setWalls} />
            <p className="text-xs text-zinc-500">{t("texture.licence_note")}</p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={showResult} disabled={!valid || busy} data-testid="texture-check">
                👁 {t("texture.check")}
              </Button>
            </div>
            {result && (
              <div className="space-y-1">
                <p className="text-xs text-zinc-500">{t("texture.result")}</p>
                <img src={result} alt="" className="max-h-48 rounded border border-zinc-200 dark:border-zinc-700" data-testid="texture-result" />
              </div>
            )}
          </div>
        </div>
        {busy && (
          <div className="flex items-center gap-2 text-xs">
            <Spinner /> {t("photos.working")}
          </div>
        )}
        {error && <Alert kind="error">{error}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={save} disabled={!valid || busy} data-testid="texture-save">
            {t("texture.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
