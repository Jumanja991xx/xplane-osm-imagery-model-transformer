import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  api,
  ApiError,
  imageryObjectUrl,
  preview,
  type ItemAttrs,
  type PreviewData,
  type PreviewObject,
} from "../api/client";
import { useT } from "../i18n";
import { CATEGORY_COLORS, categoryLabelKey } from "../scene";
import { useApp } from "../store";
import { Alert, Button, Checkbox, Spinner } from "./ui";

// Local metres (x east, y north, z up) → three.js (y up, -z north).
const toThree = (x: number, y: number, z: number) => new THREE.Vector3(x, z, -y);

const ROOF_MATERIALS = ["solar", "metal", "membrane", "tiles", "flat", "glass"] as const;

/** Interactive 3D view of everything the plan will build, before anything is written.
 *  While the plan is open (editable), single objects can be fine-tuned here. */
export function PreviewDialog({
  jobId,
  onClose,
  editable = false,
}: {
  jobId: string;
  onClose: () => void;
  editable?: boolean;
}) {
  const { t } = useT();
  const mount = useRef<HTMLDivElement>(null);
  const focusRef = useRef<(o: PreviewObject) => void>(() => {});
  const reloadRef = useRef<(o: PreviewObject) => Promise<void>>(async () => {});
  const highlightRef = useRef<(stem: string | null) => void>(() => {});
  const [data, setData] = useState<PreviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let disposed = false;
    const urls: string[] = [];
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.shadowMap.enabled = true;
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xbcd4ea);
    const camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.5, 20000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.maxPolarAngle = Math.PI * 0.49;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x667755, 1.4));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(-300, 500, 250);
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 4096);
    Object.assign(sun.shadow.camera, { left: -500, right: 500, top: 500, bottom: -500, far: 2000 });
    scene.add(sun);
    (window as unknown as { __xpsbPreview?: unknown }).__xpsbPreview = { scene, camera, renderer };

    const frame = (center: THREE.Vector3, size: number) => {
      controls.target.copy(center);
      camera.position.copy(center).add(new THREE.Vector3(-0.6, 0.55, 0.8).multiplyScalar(size * 1.4 + 20));
      controls.update();
    };
    focusRef.current = (o) => frame(toThree(o.offset[0], o.offset[1], 5), 60);

    const objects = new THREE.Group();
    scene.add(objects);
    const loader = new THREE.TextureLoader();

    const disposeMesh = (mesh: THREE.Mesh) => {
      mesh.geometry.dispose();
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.map?.dispose();
      mat.dispose();
    };

    highlightRef.current = (stem) => {
      for (const child of objects.children) {
        const mat = (child as THREE.Mesh).material as THREE.MeshStandardMaterial;
        mat.emissive.setHex(child.name === stem ? 0x2a4a7a : 0x000000);
      }
    };

    /** (Re)loads one object; replaces an older version with the same name. */
    const loadObject = async (o: PreviewObject): Promise<THREE.Mesh | null> => {
      const old = objects.getObjectByName(o.stem) as THREE.Mesh | undefined;
      if (old) {
        objects.remove(old);
        disposeMesh(old);
      }
      if (o.status !== "ok") return null;
      const [m, texUrl] = await Promise.all([preview.mesh(jobId, o.stem), preview.textureUrl(jobId, o.stem)]);
      if (disposed) return null;
      urls.push(texUrl);
      const pos: number[] = [];
      const nrm: number[] = [];
      for (let i = 0; i < m.positions.length; i += 3) {
        pos.push(m.positions[i], m.positions[i + 2], -m.positions[i + 1]);
        nrm.push(m.normals[i], m.normals[i + 2], -m.normals[i + 1]);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      geo.setAttribute("normal", new THREE.Float32BufferAttribute(nrm, 3));
      geo.setAttribute("uv", new THREE.Float32BufferAttribute(m.uvs, 2));
      geo.setIndex(m.index);
      const tex = await loader.loadAsync(texUrl);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = 8;
      // FrontSide on purpose: a face turned the wrong way would also be missing in X-Plane.
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ map: tex, roughness: 0.85 }));
      mesh.position.copy(toThree(o.offset[0], o.offset[1], 0));
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.name = o.stem;
      objects.add(mesh);
      return mesh;
    };
    reloadRef.current = async (o) => {
      await loadObject(o);
    };

    // Click (not drag) on an object selects it.
    const raycaster = new THREE.Raycaster();
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => (down = { x: e.clientX, y: e.clientY });
    const onUp = (e: PointerEvent) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const pointer = new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      );
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(objects.children, false)[0];
      setSelected(hit ? hit.object.name : null);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    const resize = () => {
      camera.aspect = el.clientWidth / el.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(el.clientWidth, el.clientHeight);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);

    let raf = 0;
    const loop = () => {
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(loop);
    };
    loop();

    (async () => {
      try {
        const d = await preview.scene(jobId);
        if (disposed) return;
        setData(d);
        // Ground: aerial image if there is one, else a neutral plane.
        if (d.imagery) {
          const url = await imageryObjectUrl(jobId).catch(() => null);
          const { sw, ne } = d.imagery;
          const geo = new THREE.PlaneGeometry(ne[0] - sw[0], ne[1] - sw[1]);
          const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1 });
          if (url) {
            urls.push(url);
            mat.map = await loader.loadAsync(url);
            mat.map.colorSpace = THREE.SRGBColorSpace;
          }
          const ground = new THREE.Mesh(geo, mat);
          ground.rotation.x = -Math.PI / 2;
          ground.position.set((sw[0] + ne[0]) / 2, -0.05, -(sw[1] + ne[1]) / 2);
          ground.receiveShadow = true;
          scene.add(ground);
        }
        // With the aerial image as ground texture the photo already is the surface.
        for (const s of d.imagery && d.surface_mode === "imagery" ? [] : d.surfaces) {
          const shape = new THREE.Shape(s.rings[0].map(([x, y]) => new THREE.Vector2(x, y)));
          shape.holes = s.rings.slice(1).map((r) => new THREE.Path(r.map(([x, y]) => new THREE.Vector2(x, y))));
          const mesh = new THREE.Mesh(
            new THREE.ShapeGeometry(shape),
            new THREE.MeshStandardMaterial({
              color: CATEGORY_COLORS[s.category as keyof typeof CATEGORY_COLORS] ?? "#888888",
              transparent: true,
              opacity: 0.55,
              depthWrite: false,
            }),
          );
          mesh.rotation.x = -Math.PI / 2;
          mesh.position.y = 0.05;
          mesh.receiveShadow = true;
          scene.add(mesh);
        }
        const box = new THREE.Box3();
        for (const o of d.objects) {
          const mesh = await loadObject(o);
          if (disposed) return;
          if (mesh) box.expandByObject(mesh);
        }
        if (!box.isEmpty()) {
          const size = box.getSize(new THREE.Vector3());
          frame(box.getCenter(new THREE.Vector3()), Math.max(size.x, size.z));
        } else {
          frame(new THREE.Vector3(), 200);
        }
      } catch (e) {
        if (!disposed) setError(String(e));
      } finally {
        if (!disposed) setLoading(false);
      }
    })();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      controls.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) disposeMesh(obj);
      });
      renderer.dispose();
      el.removeChild(renderer.domElement);
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [jobId]);

  useEffect(() => highlightRef.current(selected), [selected, data]);

  const label = (o: PreviewObject) =>
    o.name || t(categoryLabelKey({ kind: "object", category: o.category as never }));
  const problems = data?.objects.filter((o) => o.status !== "ok") ?? [];
  const built = data?.objects.filter((o) => o.status === "ok") ?? [];
  const current = data?.objects.find((o) => o.stem === selected) ?? null;

  /** After an adjustment: take the new plan entry and show the rebuilt object. */
  const onAdjusted = async (o: PreviewObject) => {
    setData((d) => d && { ...d, objects: d.objects.map((x) => (x.stem === o.stem ? o : x)) });
    await reloadRef.current(o);
    highlightRef.current(o.stem);
  };

  return (
    <div className="fixed inset-0 z-50 flex bg-black/60 p-4" role="dialog" aria-modal="true" data-testid="preview-dialog">
      <div className="flex h-full w-full overflow-hidden rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <aside className="flex w-80 shrink-0 flex-col border-r border-zinc-200 dark:border-zinc-700">
          <div className="flex items-center justify-between border-b border-zinc-200 px-3 py-2 dark:border-zinc-700">
            <h2 className="text-sm font-semibold">{t("preview.title")}</h2>
            <Button variant="ghost" onClick={onClose} data-testid="preview-close">
              {t("common.close")}
            </Button>
          </div>
          <div className="flex-1 space-y-3 overflow-y-auto p-3 text-sm">
            <p className="text-xs text-zinc-500">{t(editable ? "preview.hint_edit" : "preview.hint")}</p>
            {loading && (
              <div className="flex items-center gap-2">
                <Spinner /> {t("preview.loading")}
              </div>
            )}
            {error && <Alert kind="error">{error}</Alert>}
            {current && editable && (
              <ObjectControls key={current.stem} jobId={jobId} object={current} onAdjusted={onAdjusted} />
            )}
            {built.length > 0 && (
              <ul className="space-y-0.5" data-testid="preview-objects">
                {built.map((o) => (
                  <li key={o.stem}>
                    <button
                      className={`w-full truncate rounded px-1 text-left hover:underline ${
                        o.stem === selected ? "bg-accent-600/15 font-medium" : ""
                      }`}
                      onClick={() => {
                        setSelected(o.stem);
                        focusRef.current(o);
                      }}
                    >
                      {label(o)}
                      <span className="ml-1 text-xs text-zinc-400">{o.triangles}△</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {problems.length > 0 && (
              <div className="space-y-1">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase">{t("preview.not_built")}</h3>
                {problems.map((o) => (
                  <details key={o.stem} className="text-xs">
                    <summary>
                      {label(o)}: {t(`preview.status.${o.status}`)}
                    </summary>
                    <pre className="mt-1 whitespace-pre-wrap break-all text-zinc-500">{o.message}</pre>
                  </details>
                ))}
              </div>
            )}
          </div>
        </aside>
        <div ref={mount} className="relative min-w-0 flex-1" data-testid="preview-canvas" />
      </div>
    </div>
  );
}

/** Quick controls for one object: free, instant, only this object is rebuilt. */
function ObjectControls({
  jobId,
  object,
  onAdjusted,
}: {
  jobId: string;
  object: PreviewObject;
  onAdjusted: (o: PreviewObject) => Promise<void>;
}) {
  const { t } = useT();
  const job = useApp((s) => s.job);
  const setJob = useApp((s) => s.setJob);
  const item = job?.analysis?.items?.find((i) => i.id === object.item_id);
  const attrs = item?.attrs ?? {};
  const [height, setHeight] = useState(attrs.height_m != null ? String(attrs.height_m) : "");
  const [rows, setRows] = useState(attrs.rows != null ? String(attrs.rows) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!item) return null;
  const stand = object.category === "grandstands";
  const roof = (attrs.material ?? "").replace(/^roof:/, "");

  const apply = async (patch: Partial<ItemAttrs>) => {
    setBusy(true);
    setError(null);
    try {
      const view = await api.adjustObject(jobId, object.item_id, patch);
      setJob(view);
      const next = view.plan?.objects?.find((o) => o.item_id === object.item_id);
      if (next) await onAdjusted({ ...object, ...next });
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    } finally {
      setBusy(false);
    }
  };
  const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));

  return (
    <div
      className="space-y-2 rounded-md border border-accent-600/40 bg-accent-600/5 p-2"
      data-testid="object-controls"
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">
          {object.name || t(categoryLabelKey({ kind: "object", category: object.category as never }))}
        </span>
        {busy && <Spinner />}
      </div>
      {object.status !== "ok" && <Alert kind="warning">{object.message}</Alert>}
      <Button onClick={() => apply({ flip: !attrs.flip })} disabled={busy} data-testid="adjust-flip">
        ↻ {t("adjust.flip")}
      </Button>
      <label className="flex items-center gap-2 text-xs">
        <span className="w-24 text-zinc-600 dark:text-zinc-300">{t("adjust.height")}</span>
        <input
          className="w-20 rounded border border-zinc-300 bg-white px-1.5 py-0.5 dark:border-zinc-600 dark:bg-zinc-800"
          inputMode="decimal"
          value={height}
          placeholder={t("adjust.auto")}
          onChange={(e) => setHeight(e.target.value)}
          onBlur={() => num(height) !== (attrs.height_m ?? null) && apply({ height_m: num(height) })}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          disabled={busy}
          data-testid="adjust-height"
        />
        m
      </label>
      {stand && (
        <label className="flex items-center gap-2 text-xs">
          <span className="w-24 text-zinc-600 dark:text-zinc-300">{t("adjust.rows")}</span>
          <input
            className="w-20 rounded border border-zinc-300 bg-white px-1.5 py-0.5 dark:border-zinc-600 dark:bg-zinc-800"
            inputMode="numeric"
            value={rows}
            placeholder={t("adjust.auto")}
            onChange={(e) => setRows(e.target.value)}
            onBlur={() => num(rows) !== (attrs.rows ?? null) && apply({ rows: num(rows) })}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            disabled={busy}
            data-testid="adjust-rows"
          />
        </label>
      )}
      <Checkbox
        label={t("adjust.covered")}
        checked={Boolean(attrs.covered) || roof === "solar"}
        onChange={(on) => apply(on ? { covered: true } : { covered: false, material: null })}
      />
      <label className="flex items-center gap-2 text-xs">
        <span className="w-24 text-zinc-600 dark:text-zinc-300">{t("adjust.roof")}</span>
        <select
          className="rounded border border-zinc-300 bg-white px-1.5 py-0.5 dark:border-zinc-600 dark:bg-zinc-800"
          value={(ROOF_MATERIALS as readonly string[]).includes(roof) ? roof : ""}
          onChange={(e) => apply({ material: e.target.value ? `roof:${e.target.value}` : null })}
          disabled={busy}
          data-testid="adjust-roof"
        >
          <option value="">{t("adjust.auto")}</option>
          {ROOF_MATERIALS.map((m) => (
            <option key={m} value={m}>
              {t(`roof.${m}`)}
            </option>
          ))}
        </select>
      </label>
      {error && <Alert kind="error">{error}</Alert>}
    </div>
  );
}
