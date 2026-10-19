import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  api,
  ApiError,
  imageryObjectUrl,
  preview,
  type ItemAttrs,
  type JobView,
  type PreviewData,
  type PreviewObject,
} from "../api/client";
import { useT } from "../i18n";
import { CATEGORY_COLORS, categoryLabelKey } from "../scene";
import { useApp } from "../store";
import { PhotoPanel } from "./PhotoPanel";
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
      for (const group of objects.children) {
        group.traverse((child) => {
          if (child instanceof THREE.Mesh && !child.userData.library) {
            (child.material as THREE.MeshStandardMaterial).emissive.setHex(group.name === stem ? 0x2a4a7a : 0x000000);
          }
        });
      }
    };

    /** (Re)loads one object; replaces an older version with the same name. */
    const loadObject = async (o: PreviewObject): Promise<THREE.Object3D | null> => {
      const old = objects.getObjectByName(o.stem);
      if (old) {
        objects.remove(old);
        old.traverse((c) => c instanceof THREE.Mesh && disposeMesh(c));
      }
      if (o.status !== "ok") return null;
      const group = new THREE.Group();
      group.name = o.stem;
      group.position.copy(toThree(o.offset[0], o.offset[1], 0));
      if (o.has_mesh !== false) {
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
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        group.add(mesh);
      }
      // X-Plane library objects: X-Plane draws them; here simple stand-ins of their kind and size.
      for (const p of o.library ?? []) {
        const standIn = libraryStandIn(p);
        standIn.position.copy(toThree(p.x, p.y, 0));
        standIn.rotation.y = -(p.heading * Math.PI) / 180;
        group.add(standIn);
      }
      objects.add(group);
      return group;
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
      const hit = raycaster.intersectObjects(objects.children, true)[0];
      let target: THREE.Object3D | null = hit ? hit.object : null;
      while (target && target.parent !== objects) target = target.parent;
      setSelected(target ? target.name : null);
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
        // Ground: aerial image if there is one, else a neutral plane. With the aerial
        // image as surface texture only the surfaces carry it in X-Plane: there it is
        // shown at full brightness, the rest is dimmed (orientation only).
        const orthoMode = Boolean(d.imagery) && d.surface_mode === "imagery";
        let aerial: THREE.Texture | null = null;
        if (d.imagery) {
          const url = await imageryObjectUrl(jobId).catch(() => null);
          const { sw, ne } = d.imagery;
          const geo = new THREE.PlaneGeometry(ne[0] - sw[0], ne[1] - sw[1]);
          const mat = new THREE.MeshStandardMaterial({ color: orthoMode ? 0x858585 : 0xffffff, roughness: 1 });
          if (url) {
            urls.push(url);
            aerial = await loader.loadAsync(url);
            aerial.colorSpace = THREE.SRGBColorSpace;
            mat.map = aerial;
          }
          const ground = new THREE.Mesh(geo, mat);
          ground.rotation.x = -Math.PI / 2;
          ground.position.set((sw[0] + ne[0]) / 2, -0.05, -(sw[1] + ne[1]) / 2);
          ground.receiveShadow = true;
          scene.add(ground);
        }
        for (const s of d.surfaces) {
          const shape = new THREE.Shape(s.rings[0].map(([x, y]) => new THREE.Vector2(x, y)));
          shape.holes = s.rings.slice(1).map((r) => new THREE.Path(r.map(([x, y]) => new THREE.Vector2(x, y))));
          const geo = new THREE.ShapeGeometry(shape);
          let mat: THREE.MeshStandardMaterial;
          if (orthoMode && aerial && d.imagery) {
            const { sw, ne } = d.imagery;
            const pos = geo.getAttribute("position");
            const uv: number[] = [];
            for (let i = 0; i < pos.count; i++) {
              uv.push((pos.getX(i) - sw[0]) / (ne[0] - sw[0]), (pos.getY(i) - sw[1]) / (ne[1] - sw[1]));
            }
            geo.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
            mat = new THREE.MeshStandardMaterial({ map: aerial, roughness: 1 });
          } else {
            mat = new THREE.MeshStandardMaterial({
              color: CATEGORY_COLORS[s.category as keyof typeof CATEGORY_COLORS] ?? "#888888",
              transparent: true,
              opacity: 0.55,
              depthWrite: false,
            });
          }
          const mesh = new THREE.Mesh(geo, mat);
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
            {data?.imagery && data.surface_mode === "imagery" && (
              <p className="text-xs text-zinc-500">{t("preview.ortho_legend")}</p>
            )}
            {data?.objects.some((o) => (o.library ?? []).length > 0) && (
              <p className="text-xs text-zinc-500">{t("preview.library_legend")}</p>
            )}
            {current && (current.library ?? []).length > 0 && <LibraryList library={current.library ?? []} />}
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

type Control =
  | "flip"
  | "height"
  | "levels"
  | "rows"
  | "covered"
  | "roof_shape"
  | "roof"
  | "wall_material"
  | "wall_color"
  | "roof_color"
  | "seat_color";

// Which quick controls make sense per kind of object (the default build scripts
// read exactly these). Anything else: the AI architect below.
const CONTROLS: Record<string, Control[]> = {
  grandstands: ["flip", "height", "rows", "covered", "roof", "roof_color", "seat_color", "wall_color"],
  buildings: ["height", "levels", "roof_shape", "flip", "roof", "wall_material", "wall_color", "roof_color"],
  canopies: ["height", "roof", "roof_color"],
  masts: ["height"],
  scaffolding: ["height"],
};
const DEFAULT_CONTROLS: Control[] = ["height"];
const ROOF_SHAPES = ["flat", "gabled", "hipped"] as const;
const WALL_MATERIALS = ["windows", "plaster", "brick", "glass", "metal_sheet", "concrete", "wood"] as const;

/** Quick controls for one object: free, instant, only this object is rebuilt. */
type LibraryKind = "tree" | "light" | "vehicle" | "person" | "flag" | "fence" | "other";

const LIBRARY_WORDS: [LibraryKind, string[]][] = [
  ["tree", ["tree", "bush", "hedge", "plant", "shrub", "palm", "conifer", "deciduous"]],
  ["light", ["light", "lamp", "flood", "lantern"]],
  ["vehicle", ["car", "truck", "bus", "van", "vehicle", "tractor", "trailer", "lorry"]],
  ["person", ["people", "person", "pedestrian", "crowd"]],
  ["flag", ["flag", "banner", "billboard", "board", "screen", "sign"]],
  ["fence", ["fence", "barrier", "rail", "wall", "bollard", "gate"]],
];

export function libraryKind(path: string): LibraryKind {
  // Whole words or word starts/ends ("trees", "streetlamp"), so "street" is no "tree".
  const tokens = path.toLowerCase().split(/[^a-z]+/).filter(Boolean);
  for (const [kind, words] of LIBRARY_WORDS) {
    if (tokens.some((tok) => words.some((w) => tok.startsWith(w) || tok.endsWith(w)))) return kind;
  }
  return "other";
}

/** Recognisable stand-in for an X-Plane library object (the real look comes from X-Plane). */
function libraryStandIn(p: { path: string; size?: number[] | null }): THREE.Group {
  const [w0, d0, h0] = p.size && p.size.length >= 3 ? p.size : [1, 1, 2];
  const w = Math.max(w0, 0.2);
  const d = Math.max(d0, 0.2);
  const h = Math.max(h0, 0.2);
  const g = new THREE.Group();
  const mat = (color: number, opacity = 1) =>
    new THREE.MeshStandardMaterial({ color, roughness: 0.8, transparent: opacity < 1, opacity });
  const add = (geo: THREE.BufferGeometry, color: number, y: number, opacity = 1) => {
    const m = new THREE.Mesh(geo, mat(color, opacity));
    m.position.y = y;
    m.castShadow = true;
    m.userData.library = p.path;
    g.add(m);
    return m;
  };
  switch (libraryKind(p.path)) {
    case "tree": {
      const r = Math.max(w, d) / 2;
      add(new THREE.CylinderGeometry(Math.max(r * 0.08, 0.1), Math.max(r * 0.1, 0.12), h * 0.35, 6), 0x6b4a2b, h * 0.175);
      add(new THREE.SphereGeometry(1, 10, 8).scale(r, h * 0.38, r), 0x4f7a35, h * 0.62);
      break;
    }
    case "light":
      add(new THREE.CylinderGeometry(0.08, 0.12, h, 6), 0x777b80, h / 2);
      add(new THREE.BoxGeometry(Math.max(w, 0.5), 0.25, Math.max(d, 0.4)), 0xf2d16b, h);
      break;
    case "vehicle":
      add(new THREE.BoxGeometry(w, h * 0.55, d), 0x4d6a8c, h * 0.3);
      add(new THREE.BoxGeometry(w * 0.85, h * 0.4, d * 0.5), 0x8fa6bf, h * 0.75);
      break;
    case "person":
      add(new THREE.CylinderGeometry(0.2, 0.2, h * 0.8, 6), 0x5a5f8a, h * 0.4);
      add(new THREE.SphereGeometry(0.15, 8, 6), 0xd9b38c, h * 0.9);
      break;
    case "flag":
      add(new THREE.CylinderGeometry(0.05, 0.06, h, 6), 0x9aa0a6, h / 2);
      add(new THREE.BoxGeometry(Math.max(w, 1), Math.min(h * 0.3, 2), 0.05), 0xc0392b, h * 0.85);
      break;
    case "fence":
      add(new THREE.BoxGeometry(w, h, Math.min(d, 0.15)), 0x8a8f94, h / 2, 0.8);
      break;
    default:
      add(new THREE.BoxGeometry(w, h, d), 0xeb8c28, h / 2, 0.85);
  }
  return g;
}

/** Which X-Plane library objects the selected object places (counted per path). */
function LibraryList({ library }: { library: { path: string }[] }) {
  const { t } = useT();
  const counts = new Map<string, number>();
  for (const p of library) counts.set(p.path, (counts.get(p.path) ?? 0) + 1);
  return (
    <div className="space-y-0.5 rounded-md bg-amber-50 p-2 text-xs dark:bg-amber-500/10" data-testid="library-list">
      <div className="font-medium">{t("preview.library_title")}</div>
      <ul>
        {[...counts.entries()].map(([path, n]) => (
          <li key={path} className="truncate" title={path}>
            {n} × {t(`preview.library_kind.${libraryKind(path)}`)}{" "}
            <code className="text-[10px] text-zinc-500">{path.split("/").pop()}</code>
          </li>
        ))}
      </ul>
      <div className="text-zinc-500">{t("preview.library_hint")}</div>
    </div>
  );
}

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!item) return null;
  const attrs = item.attrs ?? {};
  // An own script of the AI architect decides itself which parameters it reads.
  const controls = item.script ? DEFAULT_CONTROLS : (CONTROLS[object.category] ?? DEFAULT_CONTROLS);
  const has = (c: Control) => controls.includes(c);
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

  return (
    <div className="space-y-2 rounded-md border border-accent-600/40 bg-accent-600/5 p-2" data-testid="object-controls">
      <div className="flex items-center justify-between">
        <span className="font-medium">
          {object.name || t(categoryLabelKey({ kind: "object", category: object.category as never }))}
        </span>
        {busy && <Spinner />}
      </div>
      {object.status !== "ok" && <Alert kind="warning">{object.message}</Alert>}
      {has("flip") && (
        <Button onClick={() => apply({ flip: !attrs.flip })} disabled={busy} data-testid="adjust-flip">
          ↻ {t(object.category === "buildings" ? "adjust.flip_ridge" : "adjust.flip")}
        </Button>
      )}
      {has("height") && (
        <NumberField
          label={t("adjust.height")}
          unit="m"
          value={attrs.height_m}
          onCommit={(v) => apply({ height_m: v })}
          disabled={busy}
          testId="adjust-height"
        />
      )}
      {has("levels") && (
        <NumberField
          label={t("adjust.levels")}
          value={attrs.levels}
          onCommit={(v) => apply({ levels: v == null ? null : Math.round(v) })}
          disabled={busy}
          testId="adjust-levels"
        />
      )}
      {has("rows") && (
        <NumberField
          label={t("adjust.rows")}
          value={attrs.rows}
          onCommit={(v) => apply({ rows: v == null ? null : Math.round(v) })}
          disabled={busy}
          testId="adjust-rows"
        />
      )}
      {has("covered") && (
        <Checkbox
          label={t("adjust.covered")}
          checked={Boolean(attrs.covered) || roof === "solar"}
          disabled={busy}
          onChange={(on) => apply(on ? { covered: true } : { covered: false, material: null })}
        />
      )}
      {has("roof_shape") && (
        <SelectField
          label={t("adjust.roof_shape")}
          value={attrs.roof_shape ?? ""}
          options={ROOF_SHAPES.map((v) => [v, t(`roof_shape.${v}`)])}
          onChange={(v) => apply({ roof_shape: v || null })}
          disabled={busy}
          testId="adjust-roof-shape"
        />
      )}
      {has("roof") && (
        <SelectField
          label={t("adjust.roof")}
          value={(ROOF_MATERIALS as readonly string[]).includes(roof) ? roof : ""}
          options={ROOF_MATERIALS.map((v) => [v, t(`roof.${v}`)])}
          onChange={(v) => apply({ material: v ? `roof:${v}` : null })}
          disabled={busy}
          testId="adjust-roof"
        />
      )}
      {has("wall_material") && (
        <SelectField
          label={t("adjust.wall_material")}
          value={attrs.wall_material ?? ""}
          options={WALL_MATERIALS.map((v) => [v, t(`wall.${v}`)])}
          onChange={(v) => apply({ wall_material: (v || null) as ItemAttrs["wall_material"] })}
          disabled={busy}
          testId="adjust-wall"
        />
      )}
      {(["wall_color", "roof_color", "seat_color"] as const)
        .filter((c) => has(c))
        .map((c) => (
          <ColorField
            key={c}
            label={t(`adjust.${c}`)}
            value={attrs[c] ?? null}
            onCommit={(v) => apply({ [c]: v })}
            disabled={busy}
          />
        ))}
      {error && <Alert kind="error">{error}</Alert>}
      <DesignPanel jobId={jobId} object={object} onAdjusted={onAdjusted} />
    </div>
  );
}

const fieldClass =
  "rounded border border-zinc-300 bg-white px-1.5 py-0.5 dark:border-zinc-600 dark:bg-zinc-800 disabled:opacity-60";

function NumberField({
  label,
  unit,
  value,
  onCommit,
  disabled,
  testId,
}: {
  label: string;
  unit?: string;
  value: number | null | undefined;
  onCommit: (v: number | null) => void;
  disabled: boolean;
  testId: string;
}) {
  const { t } = useT();
  const [text, setText] = useState(value != null ? String(value) : "");
  const parse = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-28 text-zinc-600 dark:text-zinc-300">{label}</span>
      <input
        className={`w-20 ${fieldClass}`}
        inputMode="decimal"
        value={text}
        placeholder={t("adjust.auto")}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          const v = parse(text);
          if (v !== (value ?? null) && (v == null || Number.isFinite(v))) onCommit(v);
        }}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        disabled={disabled}
        data-testid={testId}
      />
      {unit}
    </label>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
  disabled,
  testId,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
  disabled: boolean;
  testId: string;
}) {
  const { t } = useT();
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-28 text-zinc-600 dark:text-zinc-300">{label}</span>
      <select
        className={fieldClass}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        data-testid={testId}
      >
        <option value="">{t("adjust.auto")}</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Colour picker; applies once the picker has been still for a moment. */
function ColorField({
  label,
  value,
  onCommit,
  disabled,
}: {
  label: string;
  value: string | null;
  onCommit: (v: string | null) => void;
  disabled: boolean;
}) {
  const { t } = useT();
  const timer = useRef<number | undefined>(undefined);
  const [color, setColor] = useState(value && /^#[0-9a-f]{6}$/i.test(value) ? value : "#a0a0a0");
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-28 text-zinc-600 dark:text-zinc-300">{label}</span>
      <input
        type="color"
        className="h-6 w-10 cursor-pointer rounded border border-zinc-300 dark:border-zinc-600"
        value={color}
        onChange={(e) => {
          const v = e.target.value;
          setColor(v);
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => onCommit(v.toUpperCase()), 700);
        }}
        disabled={disabled}
      />
      {value ? (
        <button className="text-zinc-500 underline" onClick={() => onCommit(null)} disabled={disabled}>
          {t("adjust.auto")}
        </button>
      ) : (
        <span className="text-zinc-400">{t("adjust.auto")}</span>
      )}
    </label>
  );
}

/** Live steps of the AI architect with elapsed time, while the user waits. */
function DesignProgress({ itemId, since, started }: { itemId: string; since: number; started: number }) {
  const { t } = useT();
  const events = useApp((s) => s.events);
  const [now, setNow] = useState(Date.now());
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const steps = events.filter((e) => e.seq > since && e.stage === "design" && e.params?.item === itemId);
  const secs = Math.floor((now - started) / 1000);
  const elapsed = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
  const current = steps[steps.length - 1];
  return (
    <div
      ref={box}
      className="space-y-2 rounded-md border border-accent-500/50 bg-accent-600/10 p-2.5"
      data-testid="design-progress"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <span className="inline-block h-7 w-7 shrink-0 animate-spin rounded-full border-[3px] border-accent-500 border-t-transparent" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {current?.key ? t(current.key, current.params ?? {}) : t("design.step.sending")}
          </div>
          <div className="text-xs text-zinc-500 tabular-nums">
            {elapsed} · {t("design.running_hint")}
          </div>
        </div>
      </div>
      {steps.length > 1 && (
        <ul className="space-y-0.5 text-xs text-zinc-500">
          {steps.slice(0, -1).map((e) => (
            <li key={e.seq}>✓ {t(e.key ?? "", e.params ?? {})}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Request to the AI architect in own words: cost first, then send, then keep or reset. */
function DesignPanel({
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
  const [text, setText] = useState("");
  const [estimate, setEstimate] = useState<{ typical: number; max: number; budget: number; model: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState<{ since: number; started: number } | null>(null);
  if (!item) return null;
  const aiOn = Boolean(job?.brief?.use_ai);

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
  const showNew = async (view: JobView) => {
    setJob(view);
    const next = view.plan?.objects?.find((o) => o.item_id === object.item_id);
    if (next) await onAdjusted({ ...object, ...next });
  };
  const check = () =>
    run(async () => {
      const e = await api.designEstimate(jobId, object.item_id);
      setEstimate({ typical: e.estimate_usd, max: Math.min(e.max_usd, e.budget_usd), budget: e.budget_usd, model: e.model });
    });
  const send = () =>
    run(async () => {
      if (!estimate) return;
      const events = useApp.getState().events;
      setSending({ since: events.length ? events[events.length - 1].seq : 0, started: Date.now() });
      const out = await api.redesign(jobId, object.item_id, text.trim(), estimate.max).finally(() =>
        setSending(null),
      );
      setEstimate(null);
      setText("");
      setDone(t("design.done", { summary: out.summary, cost: out.cost_usd.toFixed(3) }));
      await showNew(out.job);
    });
  const reset = () =>
    run(async () => {
      setDone(null);
      await showNew(await api.resetDesign(jobId, object.item_id));
    });

  return (
    <div className="space-y-1.5 border-t border-zinc-200 pt-2 dark:border-zinc-700" data-testid="design-panel">
      <div className="text-xs font-semibold text-zinc-500 uppercase">✨ {t("design.title")}</div>
      {item.script && item.script_request && (
        <div className="flex items-start justify-between gap-2 text-xs">
          <span className="text-zinc-600 dark:text-zinc-300">{t("design.custom", { request: item.script_request })}</span>
          <Button variant="ghost" onClick={reset} disabled={busy} data-testid="design-reset">
            {t("design.reset")}
          </Button>
        </div>
      )}
      {!aiOn ? (
        <p className="text-xs text-zinc-500">{t("design.no_ai")}</p>
      ) : (
        <>
          <PhotoPanel jobId={jobId} item={item} />
          <textarea
            className="w-full rounded border border-zinc-300 bg-white p-1.5 text-xs dark:border-zinc-600 dark:bg-zinc-800"
            rows={3}
            value={text}
            placeholder={t("design.placeholder")}
            onChange={(e) => {
              setText(e.target.value);
              setEstimate(null);
            }}
            disabled={busy}
            data-testid="design-text"
          />
          {!estimate ? (
            <Button onClick={check} disabled={busy || text.trim().length < 3} data-testid="design-check">
              {t("design.check")}
            </Button>
          ) : (
            <div className="space-y-1.5">
              <p className="text-xs text-zinc-600 dark:text-zinc-300">
                {t("design.estimate", {
                  typical: estimate.typical.toFixed(2),
                  max: estimate.max.toFixed(2),
                  budget: estimate.budget.toFixed(2),
                  model: estimate.model,
                })}
              </p>
              <Button variant="primary" onClick={send} disabled={busy || estimate.max <= 0} data-testid="design-send">
                {t("design.send", { max: estimate.max.toFixed(2) })}
              </Button>
            </div>
          )}
          {sending && <DesignProgress itemId={object.item_id} since={sending.since} started={sending.started} />}
        </>
      )}
      {done && <Alert kind="success">{done}</Alert>}
      {error && <Alert kind="error">{error}</Alert>}
    </div>
  );
}
