import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  api,
  ApiError,
  imageryObjectUrl,
  preview,
  renderPhotoUrl,
  type ItemAttrs,
  type JobView,
  type PartChange,
  type PreviewData,
  type PreviewPart,
  type PreviewLight,
  type PreviewObject,
} from "../api/client";
import { hasKeyFor, modelGroups, modelLabel, modelName, useAiModels } from "../ai";
import { type Translate, useT } from "../i18n";
import { CATEGORY_COLORS, categoryLabelKey } from "../scene";
import { useApp } from "../store";
import { ErrorBoundary } from "./ErrorBoundary";
import { PhotoPanel } from "./PhotoPanel";
import { Alert, Button, Checkbox, Spinner } from "./ui";

const UNDO_MS = 10000;
const NO_REMOVED: NonNullable<NonNullable<JobView["plan"]>["removed"]> = [];

// Local metres (x east, y north, z up) → three.js (y up, -z north).
const toThree = (x: number, y: number, z: number) => new THREE.Vector3(x, z, -y);

const ROOF_MATERIALS = ["solar", "metal", "membrane", "tiles", "flat", "glass"] as const;

/** Interactive 3D view of everything the plan will build, before anything is written.
 *  While the plan is open (editable), single objects can be fine-tuned here. */
const SPOTS_PER_OBJECT = 10;
const NO_STEMS: string[] = [];

/** The object's X-Plane lights for the night view: a glowing point for each and
 * real spot lights for the strongest few (a browser cannot draw hundreds). */
function nightLights(lights: PreviewLight[]): THREE.Group {
  const group = new THREE.Group();
  group.userData.nightOnly = true;
  group.visible = false;
  if (!lights.length) return group;
  const pts = new THREE.BufferGeometry();
  pts.setAttribute("position", new THREE.Float32BufferAttribute(lights.flatMap((l) => [l.x, l.z, -l.y]), 3));
  group.add(new THREE.Points(pts, new THREE.PointsMaterial({ color: 0xfff2d0, size: 1.2, sizeAttenuation: true })));
  const strongest = [...lights].sort((a, b) => b.cd - a.cd);
  const step = Math.max(1, Math.floor(strongest.length / SPOTS_PER_OBJECT));
  for (let i = 0; i < strongest.length && group.children.length <= SPOTS_PER_OBJECT; i += step) {
    const l = strongest[i];
    const spot = new THREE.SpotLight(new THREE.Color(...l.color), (l.cd / 250) * step, 0, Math.acos(Math.max(-1, Math.min(1, l.cos))), 0.5, 1.2);
    spot.position.set(l.x, l.z, -l.y);
    spot.target.position.set(l.x + l.dir[0] * 10, l.z + l.dir[2] * 10, -(l.y + l.dir[1] * 10));
    group.add(spot, spot.target);
  }
  return group;
}

/** The 3D preview, protected: Escape always closes it, and if anything in it fails
 * the user sees why and can close it instead of being stuck on a dark screen. */
export function PreviewDialog(props: {
  jobId: string;
  onClose: () => void;
  editable?: boolean;
  /** After the build: objects can still be moved/turned (only the DSF is rewritten). */
  placeable?: boolean;
}) {
  const { t } = useT();
  const { onClose } = props;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <ErrorBoundary title={t("preview.crashed")} closeLabel={t("common.close")} onClose={onClose}>
      <PreviewDialogInner {...props} />
    </ErrorBoundary>
  );
}

function PreviewDialogInner({
  jobId,
  onClose,
  editable = false,
  placeable = false,
}: {
  jobId: string;
  onClose: () => void;
  editable?: boolean;
  placeable?: boolean;
}) {
  const { t } = useT();
  const mount = useRef<HTMLDivElement>(null);
  const focusRef = useRef<(o: PreviewObject) => void>(() => {});
  const reloadRef = useRef<(o: PreviewObject) => Promise<void>>(async () => {});
  const groundRef = useRef<() => Promise<void>>(async () => {});
  const highlightRef = useRef<(stems: string[]) => void>(() => {});
  const nightRef = useRef<(on: boolean) => void>(() => {});
  const [night, setNight] = useState(false);
  const nightOn = useRef(false);
  const [data, setData] = useState<PreviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  // Several objects at once (Ctrl+click, Shift+click, Ctrl+A): the same change for all.
  const [multi, setMulti] = useState<string[]>([]);
  const multiRef = useRef<string[]>([]);
  multiRef.current = multi;
  const pickRef = useRef<(stem: string | null, add: boolean) => void>(() => {});
  // Moving/turning the selected object with the mouse (plan preview only).
  const [tool, setTool] = useState<PlaceTool>("none");
  const toolRef = useRef<PlaceTool>("none");
  const selectedRef = useRef<string | null>(null);
  const placeRef = useRef<(stem: string, dx: number, dy: number, heading: number) => void>(() => {});
  toolRef.current = editable || placeable ? tool : "none";
  selectedRef.current = selected;
  // Single building parts of the selected object (tower, roof ...), picked by click.
  const [partMode, setPartMode] = useState(false);
  const [partSel, setPartSel] = useState<string[]>([]);
  const [partLists, setPartLists] = useState<Record<string, PreviewPart[]>>({});
  const partModeRef = useRef(false);
  partModeRef.current = partMode && editable;
  const partPickRef = useRef<(id: string | null, add: boolean) => void>(() => {});
  const partHighlightRef = useRef<(stem: string | null, ids: string[]) => void>(() => {});
  const [gpu, setGpu] = useState<{ name: string; software: boolean } | null>(null);
  // Where the preview camera looks from (degrees from north, above the horizon):
  // the Blender photo is taken from there.
  const viewRef = useRef<() => { azimuth: number; elevation: number }>(() => ({ azimuth: 225, elevation: 22 }));

  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let disposed = false;
    const urls: string[] = [];
    // high-performance: the dedicated graphics card on laptops with two
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      preserveDrawingBuffer: true,
      powerPreference: "high-performance",
    });
    setGpu(graphicsCard(renderer));
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.shadowMap.enabled = true;
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xbcd4ea);
    const camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.5, 20000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.maxPolarAngle = Math.PI * 0.49;
    const hemi = new THREE.HemisphereLight(0xffffff, 0x667755, 1.4);
    scene.add(hemi);
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
    viewRef.current = () => {
      const d = camera.position.clone().sub(controls.target);
      const east = d.x;
      const north = -d.z;
      const azimuth = ((THREE.MathUtils.radToDeg(Math.atan2(east, north)) % 360) + 360) % 360;
      const elevation = THREE.MathUtils.radToDeg(Math.atan2(d.y, Math.hypot(east, north)));
      return { azimuth, elevation: Math.min(85, Math.max(2, elevation)) };
    };

    const objects = new THREE.Group();
    scene.add(objects);
    const loader = new THREE.TextureLoader();

    const disposeMesh = (mesh: THREE.Mesh) => {
      mesh.geometry.dispose();
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.map?.dispose();
      mat.normalMap?.dispose();
      (mesh.userData.lit as THREE.Texture | undefined)?.dispose();
      mat.dispose();
    };

    /** Night: dark sky, the night textures glow and the object's lights shine. */
    const applyNight = (root: THREE.Object3D, on: boolean) => {
      root.traverse((child) => {
        if (child instanceof THREE.Mesh && !child.userData.library) {
          const mat = child.material as THREE.MeshStandardMaterial;
          const lit = child.userData.lit as THREE.Texture | undefined;
          mat.emissiveMap = on && lit ? lit : null;
          mat.emissive.setHex(on && lit ? 0xffffff : 0x000000);
          mat.emissiveIntensity = on ? 1.4 : 1;
          mat.needsUpdate = true;
        }
        if (child.userData.nightOnly) child.visible = on;
      });
    };
    nightRef.current = (on) => {
      nightOn.current = on;
      (scene.background as THREE.Color).setHex(on ? 0x0a0e18 : 0xbcd4ea);
      hemi.intensity = on ? 0.12 : 1.4;
      sun.intensity = on ? 0 : 2.2;
      applyNight(objects, on);
    };

    /** The chosen parts of one object, drawn over it in orange. */
    partHighlightRef.current = (stem, ids) => {
      for (const group of objects.children) {
        const old = group.getObjectByName(PART_HIGHLIGHT) as THREE.Mesh | undefined;
        if (old) {
          group.remove(old);
          disposeMesh(old);
        }
      }
      const group = stem ? objects.getObjectByName(stem) : undefined;
      const mesh = group?.children.find((c) => c instanceof THREE.Mesh && c.userData.parts) as THREE.Mesh | undefined;
      const src = mesh?.geometry.getIndex();
      if (!group || !mesh || !src || !ids.length) return;
      const index: number[] = [];
      for (const p of mesh.userData.parts as PreviewPart[]) {
        if (!ids.includes(p.id)) continue;
        for (let i = p.start; i < p.start + p.count; i++) index.push(src.getX(i));
      }
      if (!index.length) return;
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", mesh.geometry.getAttribute("position").clone());
      geo.setIndex(index);
      const overlay = new THREE.Mesh(
        geo,
        new THREE.MeshBasicMaterial({
          color: 0xff8c00,
          transparent: true,
          opacity: 0.6,
          depthWrite: false,
          polygonOffset: true,
          polygonOffsetFactor: -2,
          polygonOffsetUnits: -2,
        }),
      );
      overlay.name = PART_HIGHLIGHT;
      overlay.userData.library = true; // left alone by night and selection colours
      group.add(overlay);
    };

    highlightRef.current = (stems) => {
      for (const group of objects.children) {
        group.traverse((child) => {
          if (child instanceof THREE.Mesh && !child.userData.library && !nightOn.current) {
            (child.material as THREE.MeshStandardMaterial).emissive.setHex(
              stems.includes(group.name) ? 0x2a4a7a : 0x000000,
            );
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
      group.rotation.y = -((o.heading ?? 0) * Math.PI) / 180; // heading: clockwise from north
      if (o.has_mesh !== false) {
        const [m, texUrl, nmlUrl] = await Promise.all([
          preview.mesh(jobId, o.stem),
          preview.textureUrl(jobId, o.stem),
          preview.normalUrl(jobId, o.stem),
        ]);
        const litUrl = m.lit ? await preview.litUrl(jobId, o.stem) : null;
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
        // Normal map: joints, grooves and frames catch the light as in X-Plane 12.
        let normalMap: THREE.Texture | null = null;
        if (nmlUrl) {
          urls.push(nmlUrl);
          normalMap = await loader.loadAsync(nmlUrl).catch(() => null);
        }
        const mesh = new THREE.Mesh(
          geo,
          new THREE.MeshStandardMaterial({
            map: tex,
            roughness: 0.85,
            ...(normalMap ? { normalMap } : {}),
            // see-through roofs and panels (alpha in the texture, as in X-Plane)
            ...(m.alpha ? { transparent: true, alphaTest: 0.05, side: THREE.DoubleSide } : {}),
          }),
        );
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        mesh.userData.parts = m.parts ?? [];
        if (litUrl) {
          urls.push(litUrl);
          const lit = await loader.loadAsync(litUrl).catch(() => null);
          if (lit) {
            lit.colorSpace = THREE.SRGBColorSpace;
            mesh.userData.lit = lit;
          }
        }
        group.add(mesh);
        group.add(nightLights(m.lights ?? []));
      }
      // X-Plane library objects: X-Plane draws them; here simple stand-ins of their kind and size.
      for (const p of o.library ?? []) {
        const standIn = libraryStandIn(p);
        standIn.position.copy(toThree(p.x, p.y, 0));
        standIn.rotation.y = -(p.heading * Math.PI) / 180;
        standIn.userData.part = p.part ?? "";
        group.add(standIn);
      }
      applyNight(group, nightOn.current);
      objects.add(group);
      // after adding: the chosen parts are drawn again on the new version
      const parts = (group.children.find((c) => c.userData.parts)?.userData.parts ?? []) as PreviewPart[];
      setPartLists((prev) => ({ ...prev, [o.stem]: parts }));
      return group;
    };
    reloadRef.current = async (o) => {
      await loadObject(o);
    };

    // Click (not drag) on an object selects it. With "move"/"turn" a drag moves or
    // turns the selected object on the ground instead of turning the camera.
    const raycaster = new THREE.Raycaster();
    const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const pointerAt = (e: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      return new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      );
    };
    const groundAt = (e: PointerEvent) => {
      raycaster.setFromCamera(pointerAt(e), camera);
      return raycaster.ray.intersectPlane(groundPlane, new THREE.Vector3());
    };
    let down: { x: number; y: number } | null = null;
    let drag: {
      group: THREE.Object3D;
      start: THREE.Vector3;
      pos: THREE.Vector3;
      rot: number;
      mode: PlaceTool;
    } | null = null;
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
      const stem = selectedRef.current;
      const mode = toolRef.current;
      const group = stem ? objects.getObjectByName(stem) : undefined;
      const start = mode !== "none" && group ? groundAt(e) : null;
      if (group && start) {
        drag = { group, start, pos: group.position.clone(), rot: group.rotation.y, mode };
        controls.enabled = false;
        renderer.domElement.setPointerCapture(e.pointerId);
      }
    };
    const onMove = (e: PointerEvent) => {
      if (!drag) return;
      const p = groundAt(e);
      if (!p) return;
      const { group, start, pos, rot } = drag;
      if (drag.mode === "move") {
        group.position.set(pos.x + p.x - start.x, pos.y, pos.z + p.z - start.z);
      } else {
        const a0 = Math.atan2(start.z - pos.z, start.x - pos.x);
        const a1 = Math.atan2(p.z - pos.z, p.x - pos.x);
        const deg = Math.round(((rot - (a1 - a0)) * 180) / Math.PI); // whole degrees
        group.rotation.y = (deg * Math.PI) / 180;
      }
    };
    const onUp = (e: PointerEvent) => {
      if (drag) {
        const { group, pos, rot } = drag;
        drag = null;
        controls.enabled = true;
        const dx = group.position.x - pos.x;
        const dy = -(group.position.z - pos.z);
        if (Math.hypot(dx, dy) > 0.05 || Math.abs(group.rotation.y - rot) > 1e-4) {
          const heading = ((((-group.rotation.y * 180) / Math.PI) % 360) + 360) % 360;
          placeRef.current(group.name, dx, dy, Math.round(heading * 10) / 10);
        }
        return;
      }
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
      raycaster.setFromCamera(pointerAt(e), camera);
      const hit = raycaster.intersectObjects(objects.children, true).find((h) => h.object.name !== PART_HIGHLIGHT);
      let target: THREE.Object3D | null = hit ? hit.object : null;
      while (target && target.parent !== objects) target = target.parent;
      const add = e.ctrlKey || e.metaKey || e.shiftKey;
      if (partModeRef.current && target && target.name === selectedRef.current) {
        // A part of the selected object: by the triangle hit, or the library stand-in.
        let part: string | undefined;
        for (let o: THREE.Object3D | null = hit?.object ?? null; o && o !== target; o = o.parent) {
          if (typeof o.userData.part === "string") part = o.userData.part || undefined;
        }
        const ranges = hit?.object.userData.parts as PreviewPart[] | undefined;
        const at = (hit?.faceIndex ?? -1) * 3;
        part ??= ranges?.find((p) => at >= p.start && at < p.start + p.count)?.id;
        partPickRef.current(part ?? null, add);
        return;
      }
      pickRef.current(target ? target.name : null, e.ctrlKey || e.metaKey);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointermove", onMove);
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
          // As the package shows it: real objects painted out under their models.
          const url = await imageryObjectUrl(jobId, true).catch(() => null);
          const { sw, ne } = d.imagery;
          const geo = new THREE.PlaneGeometry(ne[0] - sw[0], ne[1] - sw[1]);
          const mat = new THREE.MeshStandardMaterial({ color: orthoMode ? 0x858585 : 0xffffff, roughness: 1 });
          if (url) {
            urls.push(url);
            aerial = await loader.loadAsync(url);
            aerial.colorSpace = THREE.SRGBColorSpace;
            mat.map = aerial;
            const shared = aerial;
            groundRef.current = async () => {
              const next = await imageryObjectUrl(jobId, true).catch(() => null);
              if (!next || disposed) return;
              urls.push(next);
              const fresh = await loader.loadAsync(next);
              shared.image = fresh.image; // the ground and the surfaces share it
              shared.needsUpdate = true;
            };
          }
          const ground = new THREE.Mesh(geo, mat);
          ground.rotation.x = -Math.PI / 2;
          ground.position.set((sw[0] + ne[0]) / 2, -0.05, -(sw[1] + ne[1]) / 2);
          ground.receiveShadow = true;
          scene.add(ground);
        } else {
          // No aerial image (no source for the area): a neutral ground, so the
          // objects stand on something instead of floating in the sky.
          const ground = new THREE.Mesh(
            new THREE.PlaneGeometry(4000, 4000),
            new THREE.MeshStandardMaterial({ color: 0x8f9a7f, roughness: 1 }),
          );
          ground.rotation.x = -Math.PI / 2;
          ground.position.y = -0.05;
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
      renderer.domElement.removeEventListener("pointermove", onMove);
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

  const shown = multi.length ? multi : selected ? [selected] : [];
  // While parts are edited the object itself is not tinted: the chosen parts stand out.
  useEffect(() => highlightRef.current(partMode && editable ? [] : shown), [selected, multi, data, partMode]); // eslint-disable-line react-hooks/exhaustive-deps
  // Another object: no parts chosen, back to whole objects.
  useEffect(() => {
    setPartSel([]);
    setPartMode(false);
  }, [selected]);
  useEffect(
    () => partHighlightRef.current(partMode ? selected : null, partSel),
    [partMode, partSel, selected, partLists],
  );
  partPickRef.current = (id, add) => {
    if (!id) {
      if (!add) setPartSel([]);
      return;
    }
    setPartSel((cur) => (add ? (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]) : [id]));
  };

  /** Click: only this one. Ctrl/Cmd+click: add or remove it. Shift+click in the list: a range. */
  const pick = (stem: string | null, add: boolean, range = false) => {
    const order = (data?.objects ?? []).filter((o) => o.status === "ok").map((o) => o.stem);
    if (!stem) {
      if (!add) {
        setSelected(null);
        setMulti([]);
      }
      return;
    }
    if (range && selected) {
      const [a, b] = [order.indexOf(selected), order.indexOf(stem)].sort((x, y) => x - y);
      setMulti([...new Set([...multiRef.current, ...order.slice(a, b + 1)])]);
    } else if (add) {
      const base = multiRef.current.length ? multiRef.current : selected ? [selected] : [];
      setMulti(base.includes(stem) ? base.filter((s) => s !== stem) : [...base, stem]);
    } else {
      setMulti([]);
    }
    setSelected(stem);
  };
  pickRef.current = (stem, add) => pick(stem, add);

  // Ctrl+A: all objects; Escape: back to one (before it closes the preview).
  useEffect(() => {
    if (!editable) return;
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, select");
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && !typing) {
        e.preventDefault();
        setMulti((data?.objects ?? []).filter((o) => o.status === "ok").map((o) => o.stem));
      } else if (e.key === "Escape" && multiRef.current.length > 1 && !typing) {
        e.stopImmediatePropagation();
        setMulti([]);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [editable, data]);

  const label = (o: PreviewObject) =>
    o.name || t(categoryLabelKey({ kind: "object", category: o.category as never }));
  const problems = data?.objects.filter((o) => o.status !== "ok") ?? [];
  const built = data?.objects.filter((o) => o.status === "ok") ?? [];
  // No new array per call: a fresh [] from a selector re-renders without end.
  const shadedStems = useApp((s) => s.job?.build?.shaded_stems) ?? NO_STEMS;
  const current = data?.objects.find((o) => o.stem === selected) ?? null;

  /** After an adjustment: take the new plan entry and show the rebuilt object. */
  const onAdjusted = async (o: PreviewObject) => {
    setData((d) => d && { ...d, objects: d.objects.map((x) => (x.stem === o.stem ? o : x)) });
    await reloadRef.current(o);
    if (!partModeRef.current) highlightRef.current(multiRef.current.length ? multiRef.current : [o.stem]);
  };
  const many = editable && multi.length > 1;
  const chosen = (data?.objects ?? []).filter((o) => multi.includes(o.stem));

  /** Moves (metres east/north, relative) and turns (absolute heading) one object. */
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [placedNote, setPlacedNote] = useState(false);
  const place = async (stem: string, dx: number, dy: number, heading: number) => {
    const o = data?.objects.find((x) => x.stem === stem);
    const item = useApp.getState().job?.analysis?.items?.find((i) => i.id === o?.item_id);
    if (!o || !item) return;
    const a = item.attrs ?? {};
    const round = (v: number) => Math.round(v * 100) / 100;
    setPlaceError(null);
    try {
      const placement = {
        shift_x_m: round((a.shift_x_m ?? 0) + dx),
        shift_y_m: round((a.shift_y_m ?? 0) + dy),
        turn_deg: round(heading),
      };
      const view = editable
        ? await api.adjustObject(jobId, o.item_id, placement)
        : await api.placeBuilt(jobId, o.item_id, placement);
      if (!editable) setPlacedNote(true);
      useApp.getState().setJob(view);
      const next = view.plan?.objects?.find((x) => x.item_id === o.item_id);
      await onAdjusted({ ...o, ...next, offset: [o.offset[0] + dx, o.offset[1] + dy] });
    } catch (e) {
      setPlaceError(e instanceof ApiError ? t(e.key, e.params) : String(e));
      await reloadRef.current(o); // back to where it was
    }
  };
  placeRef.current = (stem, dx, dy, heading) => void place(stem, dx, dy, heading);

  /** Deletes the object (asked first): out of the plan, or out of the built DSF. */
  const [askDelete, setAskDelete] = useState<string | null>(null);
  // Undo: the last deletion for a few seconds (and Ctrl+Z), all of them in the list.
  const removed = useApp((s) => s.job?.plan?.removed) ?? NO_REMOVED;
  const items = useApp((s) => s.job?.analysis?.items);
  const [undoNote, setUndoNote] = useState<{ itemId: string; name: string } | null>(null);
  useEffect(() => {
    if (!undoNote) return;
    const timer = window.setTimeout(() => setUndoNote(null), UNDO_MS);
    return () => window.clearTimeout(timer);
  }, [undoNote]);
  const nameOf = (itemId: string, category?: string) =>
    items?.find((i) => i.id === itemId)?.name ?? (category ? t(categoryLabelKey({ kind: "object", category: category as never })) : itemId);
  const restoreObject = async (itemId: string) => {
    setPlaceError(null);
    setUndoNote(null);
    try {
      const view = await api.restoreObject(jobId, itemId);
      useApp.getState().setJob(view);
      const scene = await preview.scene(jobId);
      const back = scene.objects.filter((x) => x.item_id === itemId);
      for (const o of back) await reloadRef.current(o);
      setData((d) => d && { ...d, objects: [...d.objects.filter((x) => x.item_id !== itemId), ...back] });
      if (!editable) setPlacedNote(true);
    } catch (e) {
      setPlaceError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    }
  };
  useEffect(() => {
    if (!(editable || placeable)) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest("input, textarea, select")) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && removed.length) {
        e.preventDefault();
        void restoreObject(undoNote?.itemId ?? removed[removed.length - 1].item_id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editable, placeable, removed, undoNote]);
  const removeObject = async (stem: string) => {
    const o = data?.objects.find((x) => x.stem === stem);
    setAskDelete(null);
    if (!o) return;
    setPlaceError(null);
    try {
      const view = await api.removeObject(jobId, o.item_id);
      useApp.getState().setJob(view);
      for (const g of data?.objects.filter((x) => x.item_id === o.item_id) ?? []) {
        await reloadRef.current({ ...g, status: "skipped" }); // gone from the scene
      }
      setData((d) => d && { ...d, objects: d.objects.filter((x) => x.item_id !== o.item_id) });
      setSelected(null);
      setMulti([]);
      if (!editable) setPlacedNote(true);
      setUndoNote({ itemId: o.item_id, name: label(o) });
    } catch (e) {
      setPlaceError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    }
  };

  // Quick keys on the selected object: R / Shift+R turn by 15 degrees, Delete asks
  // to delete it (Enter confirms, Escape cancels).
  useEffect(() => {
    if (!(editable || placeable) || !selected) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest("input, textarea, select")) return;
      const o = data?.objects.find((x) => x.stem === selected);
      if (!o) return;
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        void place(selected, 0, 0, (((o.heading ?? 0) + (e.shiftKey ? -15 : 15)) % 360 + 360) % 360);
      } else if (e.key === "Delete" || (e.key === "Backspace" && e.metaKey)) {
        e.preventDefault();
        setAskDelete(selected);
      } else if (e.key === "Enter" && askDelete === selected) {
        e.preventDefault();
        void removeObject(selected);
      } else if (e.key === "Escape" && askDelete) {
        e.stopImmediatePropagation();
        setAskDelete(null);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editable, placeable, selected, data, askDelete]);

  return (
    <div className="fixed inset-0 z-50 flex bg-black/60 p-4" role="dialog" aria-modal="true" data-testid="preview-dialog">
      <div className="flex h-full w-full overflow-hidden rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <aside className="flex w-80 shrink-0 flex-col border-r border-zinc-200 dark:border-zinc-700">
          <div className="flex items-center justify-between border-b border-zinc-200 px-3 py-2 dark:border-zinc-700">
            <h2 className="text-sm font-semibold">{t("preview.title")}</h2>
            <Button
              variant="ghost"
              onClick={() => {
                nightRef.current(!night);
                setNight(!night);
              }}
              aria-pressed={night}
              data-testid="preview-night"
            >
              {night ? "☀️" : "🌙"} {t("preview.night")}
            </Button>
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
            {gpu?.software && <Alert kind="warning">{t("preview.gpu_software", { name: gpu.name })}</Alert>}
            {data && (
              <BlenderPhoto jobId={jobId} itemId={current?.item_id ?? null} view={() => viewRef.current()} />
            )}
            {data?.imagery && data.surface_mode === "imagery" && (
              <p className="text-xs text-zinc-500">{t("preview.ortho_legend")}</p>
            )}
            {data?.objects.some((o) => (o.library ?? []).length > 0) && (
              <p className="text-xs text-zinc-500">{t("preview.library_legend")}</p>
            )}
            {current && (current.library ?? []).length > 0 && <LibraryList library={current.library ?? []} />}
            {editable && built.length > 1 && (
              <div className="flex items-center justify-between text-xs text-zinc-500">
                <span>{t("multi.hint")}</span>
                <button
                  type="button"
                  className="underline"
                  onClick={() => setMulti(built.map((o) => o.stem))}
                  data-testid="select-all"
                >
                  {t("multi.select_all")}
                </button>
              </div>
            )}
            {many && (
              <MultiControls
                jobId={jobId}
                objects={chosen}
                onClear={() => setMulti([])}
                onAdjusted={async (list) => {
                  for (const o of list) await onAdjusted(o);
                }}
              />
            )}
            {current && (editable || placeable) && !many && (
              <PlacementControls
                object={current}
                tool={tool}
                setTool={setTool}
                onPlace={(dx, dy, heading) => place(current.stem, dx, dy, heading)}
                error={placeError}
                asking={askDelete === current.stem}
                onAskDelete={(on) => setAskDelete(on ? current.stem : null)}
                onDelete={() => removeObject(current.stem)}
              />
            )}
            {placeable && placedNote && (
              <Alert kind="info">
                {t("place.after_build")}
              </Alert>
            )}
            {current && editable && !many && (
              <PartsPanel
                key={`parts-${current.stem}`}
                jobId={jobId}
                object={current}
                parts={partLists[current.stem] ?? []}
                active={partMode}
                setActive={setPartMode}
                selection={partSel}
                setSelection={setPartSel}
                onAdjusted={onAdjusted}
              />
            )}
            {current && editable && !many && (
              <ObjectControls
                key={current.stem}
                jobId={jobId}
                object={current}
                onAdjusted={onAdjusted}
                onGround={() => groundRef.current()}
                parts={partMode ? partSel : []}
                partNames={(partLists[current.stem] ?? [])
                  .filter((p) => partMode && partSel.includes(p.id))
                  .map((p) => partName(t, p))}
              />
            )}
            {built.length > 0 && (
              <ul className="space-y-0.5" data-testid="preview-objects">
                {built.map((o) => (
                  <li key={o.stem}>
                    <button
                      className={`w-full truncate rounded px-1 text-left hover:underline ${
                        shown.includes(o.stem) ? "bg-accent-600/15 font-medium" : ""
                      }`}
                      onClick={(e) => {
                        pick(o.stem, e.ctrlKey || e.metaKey, e.shiftKey);
                        if (!e.ctrlKey && !e.metaKey && !e.shiftKey) focusRef.current(o);
                      }}
                    >
                      {label(o)}
                      <span className="ml-1 text-xs text-zinc-400">{o.triangles}△</span>
                      {shadedStems.includes(o.stem) && (
                        <span className="ml-1 text-xs" title={t("preview.blender_shaded")}>
                          🎨
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {(editable || placeable) && removed.length > 0 && (
              <div className="space-y-1" data-testid="preview-removed">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase">
                  {t("place.removed", { count: removed.length })}
                </h3>
                <ul className="space-y-0.5 text-sm">
                  {removed.map((o) => (
                    <li key={o.stem} className="flex items-center justify-between gap-2">
                      <span className="truncate text-zinc-500 line-through">{nameOf(o.item_id, o.category)}</span>
                      <Button variant="ghost" onClick={() => restoreObject(o.item_id)} data-testid={`restore-${o.item_id}`}>
                        ↺ {t("place.restore")}
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
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
        <div className="relative min-w-0 flex-1">
          <div ref={mount} className="absolute inset-0" data-testid="preview-canvas" />
          {undoNote && (
            <div
              className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-3 rounded-md bg-zinc-900/90 px-4 py-2 text-sm text-white shadow-lg"
              data-testid="undo-note"
            >
              <span>{t("place.deleted", { name: undoNote.name })}</span>
              <button
                type="button"
                className="font-semibold text-accent-100 underline"
                onClick={() => restoreObject(undoNote.itemId)}
                data-testid="undo-delete"
              >
                {t("place.undo")}
              </button>
              <span className="text-xs text-zinc-400">Ctrl+Z</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** The same change for several objects (Ctrl+A, Ctrl+click): quick controls and the AI. */
function MultiControls({
  jobId,
  objects,
  onClear,
  onAdjusted,
}: {
  jobId: string;
  objects: PreviewObject[];
  onClear: () => void;
  onAdjusted: (list: PreviewObject[]) => Promise<void>;
}) {
  const { t } = useT();
  const setJob = useApp((s) => s.setJob);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [estimate, setEstimate] = useState<{ estimate_usd: number; max_usd: number; budget_usd: number; model: string } | null>(null);
  const [results, setResults] = useState<{ item_id: string; ok: boolean; summary: string; error: string | null }[] | null>(null);
  const [cost, setCost] = useState<{ usd: number; model: string } | null>(null);
  const aiReady = useApp((s) => Boolean(s.job?.brief?.use_ai && (s.system?.has_api_key || s.system?.has_gemini_key)));
  const jobModel = useApp((s) => s.job?.brief?.ai_model ?? "");
  const [model, setModel] = useState(jobModel);
  const ids = objects.map((o) => o.item_id);
  const names = new Map(objects.map((o) => [o.item_id, o.name || o.stem]));

  /** Takes the new plan entries and shows every rebuilt object. */
  const refresh = async (view: JobView) => {
    setJob(view);
    const next = objects.map((o) => {
      const p = view.plan?.objects?.find((x) => x.item_id === o.item_id);
      return p ? { ...o, ...p, offset: o.offset } : o;
    });
    await onAdjusted(next);
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
  const adjust = (attrs: Partial<ItemAttrs>, factor?: number) =>
    run(async () => refresh(await api.adjustMany(jobId, ids, attrs, factor)));
  const estimateWith = (m: string) => run(async () => setEstimate(await api.designManyEstimate(jobId, ids, m)));
  const pickModel = (m: string) => {
    setModel(m);
    setEstimate(null);
    if (text.trim().length >= 3) void estimateWith(m);
  };

  return (
    <div className="space-y-2 rounded-md border border-accent-600/40 bg-accent-600/5 p-2" data-testid="multi-controls">
      <div className="flex items-center justify-between">
        <span className="font-medium">{t("multi.title", { count: objects.length })}</span>
        <span className="flex items-center gap-2">
          {busy && <Spinner />}
          <button type="button" className="text-xs underline" onClick={onClear}>
            {t("multi.clear")}
          </button>
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1 text-xs">
        <span className="mr-1">{t("adjust.height")}:</span>
        {[0.8, 0.9, 1.1, 1.2].map((f) => (
          <button
            key={f}
            type="button"
            disabled={busy}
            className="rounded border border-zinc-300 px-1.5 py-0.5 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:hover:bg-zinc-800"
            onClick={() => void adjust({}, f)}
            data-testid={`multi-height-${Math.round((f - 1) * 100)}`}
          >
            {f < 1 ? "−" : "+"}
            {Math.round(Math.abs(f - 1) * 100)} %
          </button>
        ))}
      </div>
      <NumberField
        label={t("multi.height_all")}
        unit="m"
        value={null}
        onCommit={(v) => v != null && void adjust({ height_m: v })}
        disabled={busy}
        testId="multi-height"
      />
      <NumberField
        label={t("adjust.levels")}
        value={null}
        onCommit={(v) => v != null && void adjust({ levels: Math.round(v) })}
        disabled={busy}
        testId="multi-levels"
      />
      <SelectField
        label={t("adjust.roof_shape")}
        value=""
        options={ROOF_SHAPES.map((v) => [v, t(`roof_shape.${v}`)])}
        onChange={(v) => v && void adjust({ roof_shape: v })}
        disabled={busy}
        testId="multi-roof-shape"
      />
      <SelectField
        label={t("adjust.wall_material")}
        value=""
        options={WALL_MATERIALS.map((v) => [v, t(`wall.${v}`)])}
        onChange={(v) => v && void adjust({ wall_material: v as ItemAttrs["wall_material"] })}
        disabled={busy}
        testId="multi-wall"
      />
      {(["wall_color", "roof_color"] as const).map((c) => (
        <ColorField key={c} label={t(`adjust.${c}`)} value={null} onCommit={(v) => void adjust({ [c]: v })} disabled={busy} />
      ))}
      <div className="space-y-1 border-t border-zinc-200 pt-2 dark:border-zinc-700">
        <div className="text-xs font-semibold text-zinc-500 uppercase">✨ {t("multi.ai_title")}</div>
        {!aiReady && <p className="text-xs text-zinc-500">{t("design.no_ai")}</p>}
        {aiReady && (
          <>
        <textarea
          className="w-full rounded border border-zinc-300 bg-white p-1.5 text-xs dark:border-zinc-600 dark:bg-zinc-800"
          rows={3}
          value={text}
          placeholder={t("multi.ai_placeholder")}
          onChange={(e) => {
            setText(e.target.value);
            setEstimate(null);
          }}
          data-testid="multi-ai-text"
        />
        <ModelSelect value={model} onChange={pickModel} disabled={busy} testId="multi-ai-model" />
        {!estimate ? (
          <Button
            disabled={busy || text.trim().length < 3}
            onClick={() => void estimateWith(model)}
            data-testid="multi-ai-estimate"
          >
            {t("multi.ai_estimate")}
          </Button>
        ) : (
          <>
            <p className="text-xs text-zinc-600 dark:text-zinc-300">
              {t("multi.ai_cost", {
                count: objects.length,
                typical: estimate.estimate_usd.toFixed(2),
                max: estimate.max_usd.toFixed(2),
                budget: estimate.budget_usd.toFixed(2),
                model: modelName(t, estimate.model),
              })}
            </p>
            <Button
              variant="primary"
              disabled={busy || estimate.max_usd > estimate.budget_usd}
              onClick={() =>
                void run(async () => {
                  setResults(null);
                  const out = await api.designMany(jobId, ids, text.trim(), estimate.max_usd, estimate.model);
                  setResults(out.results as never);
                  setCost({ usd: out.cost_usd, model: out.model });
                  setEstimate(null);
                  await refresh(out.job);
                })
              }
              data-testid="multi-ai-send"
            >
              {t("design.send", { max: estimate.max_usd.toFixed(2) })}
            </Button>
            {estimate.max_usd > estimate.budget_usd && <Alert kind="warning">{t("multi.ai_over_budget")}</Alert>}
          </>
        )}
        {busy && text && !estimate && results === null && <p className="text-xs text-zinc-500">{t("multi.ai_running")}</p>}
        {results && (
          <ul className="space-y-0.5 text-xs" data-testid="multi-ai-results">
            {results.map((r) => (
              <li key={r.item_id} className={r.ok ? "" : "text-red-600"}>
                {r.ok ? "✓" : "✗"} {names.get(r.item_id)}: {r.ok ? r.summary : t(r.error ?? "error.architect_failed")}
              </li>
            ))}
            {cost !== null && (
              <li className="text-zinc-500">{t("multi.ai_spent", { usd: cost.usd.toFixed(3), model: modelName(t, cost.model) })}</li>
            )}
          </ul>
        )}
        {results?.some((r) => !r.ok) && !busy && <TryStronger current={cost?.model ?? model} onPick={pickModel} />}
          </>
        )}
      </div>
      {error && <Alert kind="error">{error}</Alert>}
    </div>
  );
}

type PlaceTool = "none" | "move" | "turn";

/** Moving and turning the selected object on the ground (whole object, no rebuild). */
function PlacementControls({
  object,
  tool,
  setTool,
  onPlace,
  error,
  asking,
  onAskDelete,
  onDelete,
}: {
  object: PreviewObject;
  tool: PlaceTool;
  setTool: (t: PlaceTool) => void;
  onPlace: (dx: number, dy: number, heading: number) => Promise<void>;
  error: string | null;
  asking: boolean;
  onAskDelete: (on: boolean) => void;
  onDelete: () => Promise<void>;
}) {
  const { t } = useT();
  const item = useApp((s) => s.job?.analysis?.items?.find((i) => i.id === object.item_id));
  const [busy, setBusy] = useState(false);
  const heading = object.heading ?? 0;
  const shiftX = item?.attrs?.shift_x_m ?? 0;
  const shiftY = item?.attrs?.shift_y_m ?? 0;
  const run = async (dx: number, dy: number, h: number) => {
    setBusy(true);
    try {
      await onPlace(dx, dy, ((h % 360) + 360) % 360);
    } finally {
      setBusy(false);
    }
  };
  const toolButton = (mode: PlaceTool, label: string) => (
    <Button
      variant={tool === mode ? "primary" : "secondary"}
      onClick={() => setTool(tool === mode ? "none" : mode)}
      aria-pressed={tool === mode}
      data-testid={`place-${mode}`}
    >
      {label}
    </Button>
  );
  const nudge = (label: string, dx: number, dy: number, dh: number, title: string) => (
    <button
      type="button"
      className="rounded border border-zinc-300 px-1.5 py-0.5 text-xs hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:hover:bg-zinc-800"
      disabled={busy}
      title={title}
      onClick={() => void run(dx, dy, heading + dh)}
    >
      {label}
    </button>
  );
  return (
    <div className="space-y-2 rounded-md border border-zinc-200 p-2 dark:border-zinc-700" data-testid="placement">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold">{t("place.title")}</span>
        {busy && <Spinner />}
      </div>
      <div className="flex gap-2">
        {toolButton("move", `✋ ${t("place.move")}`)}
        {toolButton("turn", `⟳ ${t("place.turn")}`)}
      </div>
      {tool !== "none" && <p className="text-xs text-zinc-500">{t(`place.hint_${tool}`)}</p>}
      <div className="flex flex-wrap items-center gap-1">
        {nudge("←", -1, 0, 0, t("place.west"))}
        {nudge("↑", 0, 1, 0, t("place.north"))}
        {nudge("↓", 0, -1, 0, t("place.south"))}
        {nudge("→", 1, 0, 0, t("place.east"))}
        <span className="mx-1 text-zinc-300">|</span>
        {nudge("⟲ 15°", 0, 0, -15, t("place.left"))}
        {nudge("⟳ 15°", 0, 0, 15, t("place.right"))}
        {nudge("⟳ 90°", 0, 0, 90, t("place.right"))}
      </div>
      <div className="flex items-center gap-2">
        {asking ? (
          <>
            <span className="text-xs">{t("place.delete_ask")}</span>
            <Button variant="primary" onClick={() => void onDelete()} data-testid="delete-confirm">
              {t("place.delete_yes")}
            </Button>
            <Button variant="ghost" onClick={() => onAskDelete(false)}>
              {t("common.cancel")}
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={() => onAskDelete(true)} data-testid="delete-object">
            🗑 {t("place.delete")}
          </Button>
        )}
      </div>
      <p className="text-[11px] text-zinc-400">{t("place.keys")}</p>
      <p className="text-xs text-zinc-500" data-testid="placement-state">
        {t("place.state", { heading: heading.toFixed(1), east: shiftX.toFixed(1), north: shiftY.toFixed(1) })}
      </p>
      {(heading !== 0 || shiftX !== 0 || shiftY !== 0) && (
        <Button variant="ghost" onClick={() => void run(-shiftX, -shiftY, 0)} disabled={busy} data-testid="place-reset">
          {t("place.reset")}
        </Button>
      )}
      {error && <Alert kind="error">{error}</Alert>}
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
  | "seat_color"
  | "monument"
  | "width"
  | "bridge";

// Which quick controls make sense per kind of object (the default build scripts
// read exactly these). Anything else: the AI architect below.
const CONTROLS: Record<string, Control[]> = {
  grandstands: ["flip", "height", "rows", "covered", "roof", "roof_color", "seat_color", "wall_color"],
  buildings: ["height", "levels", "roof_shape", "flip", "roof", "wall_material", "wall_color", "roof_color"],
  canopies: ["height", "roof", "roof_color"],
  masts: ["height"],
  monuments: ["height", "flip", "monument"],
  scaffolding: ["height"],
  bridges: ["height", "width", "bridge"],
};
const BRIDGE_STRUCTURES = ["beam", "arch", "truss", "suspension", "cable_stayed"] as const;
const BRIDGE_MATERIALS = ["concrete", "stone", "brick", "steel", "wood"] as const;
const DEFAULT_CONTROLS: Control[] = ["height"];
const ROOF_SHAPES = ["flat", "gabled", "hipped"] as const;
const MONUMENT_KINDS = ["statue", "rider", "bust", "obelisk", "column", "stone"] as const;
const POSES = ["standing", "arm_raised", "arms_raised", "pointing", "seated"] as const;
const HELD = ["none", "palm", "wreath", "sword", "torch", "book", "flag"] as const;
const WALL_MATERIALS = [
  "windows",
  "windows_classic",
  "windows_arched",
  "windows_house",
  "plaster",
  "stone",
  "brick",
  "glass",
  "metal_sheet",
  "concrete",
  "wood",
] as const;

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
  onGround,
  parts = [],
  partNames = [],
}: {
  jobId: string;
  object: PreviewObject;
  onAdjusted: (o: PreviewObject) => Promise<void>;
  onGround: () => Promise<void>;
  parts?: string[];
  partNames?: string[];
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
      {has("width") && (
        <NumberField
          label={t("adjust.width")}
          unit="m"
          value={attrs.width_m}
          onCommit={(v) => apply({ width_m: v })}
          disabled={busy}
          testId="adjust-width"
        />
      )}
      {has("bridge") && (
        <>
          <SelectField
            label={t("adjust.bridge_structure")}
            value={attrs.bridge_structure ?? ""}
            options={BRIDGE_STRUCTURES.map((v) => [v, t(`bridge.${v}`)])}
            onChange={(v) => apply({ bridge_structure: (v || null) as ItemAttrs["bridge_structure"] })}
            disabled={busy}
            testId="adjust-bridge-structure"
          />
          <SelectField
            label={t("adjust.bridge_material")}
            value={(BRIDGE_MATERIALS as readonly string[]).includes(attrs.material ?? "") ? (attrs.material as string) : ""}
            options={BRIDGE_MATERIALS.map((v) => [v, t(`bridge_material.${v}`)])}
            onChange={(v) => apply({ material: v || null })}
            disabled={busy}
            testId="adjust-bridge-material"
          />
        </>
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
      {has("monument") && item.scan && (
        <div className="space-y-1 rounded bg-emerald-50 p-1.5 text-xs dark:bg-emerald-500/10" data-testid="scan-info">
          <div>
            {t("adjust.scan_info", {
              author: item.scan.author ?? "?",
              license: item.scan.license ?? "?",
            })}
          </div>
          <Checkbox
            label={t("adjust.use_scan")}
            checked={attrs.use_scan !== false}
            disabled={busy}
            onChange={(on) => apply({ use_scan: on })}
          />
        </div>
      )}
      {has("monument") && (
        <>
          <SelectField
            label={t("adjust.monument_kind")}
            value={attrs.monument_kind ?? ""}
            options={MONUMENT_KINDS.map((v) => [v, t(`monument.${v}`)])}
            onChange={(v) => apply({ monument_kind: (v || null) as ItemAttrs["monument_kind"] })}
            disabled={busy}
            testId="adjust-monument-kind"
          />
          {["statue", "column", undefined, null].includes(attrs.monument_kind) && (
            <>
              <SelectField
                label={t("adjust.pose")}
                value={attrs.pose ?? ""}
                options={POSES.map((v) => [v, t(`pose.${v}`)])}
                onChange={(v) => apply({ pose: (v || null) as ItemAttrs["pose"] })}
                disabled={busy}
                testId="adjust-pose"
              />
              <SelectField
                label={t("adjust.holding")}
                value={attrs.holding ?? ""}
                options={HELD.map((v) => [v, t(`held.${v}`)])}
                onChange={(v) => apply({ holding: (v || null) as ItemAttrs["holding"] })}
                disabled={busy}
                testId="adjust-holding"
              />
              <Checkbox
                label={t("adjust.robe")}
                checked={Boolean(attrs.robe)}
                disabled={busy}
                onChange={(on) => apply({ robe: on })}
              />
            </>
          )}
          <SelectField
            label={t("adjust.statue_material")}
            value={["bronze", "stone"].includes(attrs.material ?? "") ? (attrs.material as string) : ""}
            options={["bronze", "stone"].map((v) => [v, t(`statue_material.${v}`)])}
            onChange={(v) => apply({ material: v || null })}
            disabled={busy}
            testId="adjust-statue-material"
          />
        </>
      )}
      <Checkbox
        label={t("adjust.hide_below")}
        checked={attrs.hide_below ?? object.category === "bridges"}
        disabled={busy}
        onChange={(on) => void apply({ hide_below: on }).then(onGround)}
      />
      {error && <Alert kind="error">{error}</Alert>}
      <DesignPanel jobId={jobId} object={object} onAdjusted={onAdjusted} parts={parts} partNames={partNames} />
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

const PART_HIGHLIGHT = "part-highlight";

/** The graphics card WebGL draws with; software rendering (no driver, remote
 * desktop, blocked GPU) is slow and gets a warning. */
function graphicsCard(renderer: THREE.WebGLRenderer): { name: string; software: boolean } | null {
  try {
    const gl = renderer.getContext();
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const name = String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? "");
    return { name, software: /swiftshader|llvmpipe|softpipe|basic render|software/i.test(name) };
  } catch {
    return null;
  }
}

/** "walls 2" -> "Wände 2"; unnamed parts after their tool ("Zylinder 2"). */
function partName(t: Translate, p: PreviewPart): string {
  const m = /^([a-z_]+)( \d+)?$/.exec(p.label);
  if (m) {
    const key = `part_name.${m[1]}`;
    const known = t(key);
    if (known !== key) return known + (m[2] ?? "");
  }
  if (p.label) return p.label;
  const key = `part_tool.${p.tool}`;
  const known = t(key);
  return `${known !== key ? known : p.tool} ${Number(p.id.split("-").pop() ?? 0) + 1}`;
}

/** The kind of a part: its name without the number, or its tool. */
const partKind = (p: PreviewPart) => (p.label ? p.label.replace(/ \d+$/, "") : `tool:${p.tool}`);

const chipClass = (on: boolean) =>
  `rounded border px-1.5 py-0.5 text-xs ${
    on
      ? "border-orange-500 bg-orange-500/20 font-medium"
      : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-600 dark:hover:bg-zinc-800"
  }`;
const smallButton =
  "rounded border border-zinc-300 px-1.5 py-0.5 text-xs hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:hover:bg-zinc-800";

/** Single parts of one object: pick in the picture or the list (Ctrl/Shift: several),
 * then move, turn, resize or hide them; every change can be taken back. */
function PartsPanel({
  jobId,
  object,
  parts,
  active,
  setActive,
  selection,
  setSelection,
  onAdjusted,
}: {
  jobId: string;
  object: PreviewObject;
  parts: PreviewPart[];
  active: boolean;
  setActive: (on: boolean) => void;
  selection: string[];
  setSelection: (ids: string[]) => void;
  onAdjusted: (o: PreviewObject) => Promise<void>;
}) {
  const { t } = useT();
  const job = useApp((s) => s.job);
  const setJob = useApp((s) => s.setJob);
  const item = job?.analysis?.items?.find((i) => i.id === object.item_id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState(1);
  const ops = item?.part_ops?.length ?? 0;
  if (!item || (parts.length < 2 && !ops)) return null;

  const run = async (fn: () => Promise<JobView>) => {
    setBusy(true);
    setError(null);
    try {
      const view = await fn();
      setJob(view);
      const next = view.plan?.objects?.find((o) => o.item_id === object.item_id);
      if (next) await onAdjusted({ ...object, ...next });
    } catch (e) {
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
    } finally {
      setBusy(false);
    }
  };
  const change = (c: Omit<PartChange, "parts">) =>
    void run(() => api.changeParts(jobId, object.item_id, { parts: selection, ...c }));
  /** Directions on the map, turned into the object's own axes (it may be turned). */
  const move = (east: number, north: number, up = 0) => {
    const h = ((object.heading ?? 0) * Math.PI) / 180;
    const r = (v: number) => Math.round(v * 1000) / 1000;
    change({ move: [r(east * Math.cos(h) - north * Math.sin(h)), r(east * Math.sin(h) + north * Math.cos(h)), up] });
  };
  const toggle = (id: string, add: boolean) =>
    setSelection(add ? (selection.includes(id) ? selection.filter((x) => x !== id) : [...selection, id]) : [id]);
  const first = parts.find((p) => p.id === selection[0]);
  const sameKind = first ? parts.filter((p) => partKind(p) === partKind(first)).map((p) => p.id) : [];

  return (
    <div className="space-y-2 rounded-md border border-orange-500/40 bg-orange-500/5 p-2" data-testid="parts-panel">
      <div className="flex items-center justify-between">
        <span className="font-medium">🧩 {t("parts.title")}</span>
        <span className="flex items-center gap-2">
          {busy && <Spinner />}
          <Button
            variant={active ? "primary" : "ghost"}
            aria-pressed={active}
            onClick={() => {
              setActive(!active);
              setSelection([]);
            }}
            data-testid="parts-toggle"
          >
            {t(active ? "parts.done" : "parts.edit")}
          </Button>
        </span>
      </div>
      {!active ? (
        <p className="text-xs text-zinc-500">{t("parts.intro")}</p>
      ) : (
        <>
          <p className="text-xs text-zinc-500">{t("parts.hint")}</p>
          <div className="flex flex-wrap gap-1" data-testid="parts-list">
            {parts.map((p) => (
              <button
                key={p.id}
                type="button"
                className={chipClass(selection.includes(p.id))}
                onClick={(e) => toggle(p.id, e.ctrlKey || e.metaKey || e.shiftKey)}
                data-testid={`part-${p.id}`}
              >
                {partName(t, p)}
              </button>
            ))}
          </div>
          <div className="flex gap-3 text-xs">
            <button type="button" className="underline" onClick={() => setSelection(parts.map((p) => p.id))}>
              {t("parts.all")}
            </button>
            {sameKind.length > 1 && selection.length === 1 && (
              <button type="button" className="underline" onClick={() => setSelection(sameKind)} data-testid="parts-same">
                {t("parts.same_kind", { count: sameKind.length })}
              </button>
            )}
          </div>
          {selection.length > 0 && (
            <div className="space-y-1.5 text-xs" data-testid="parts-controls">
              <div className="font-medium">{t("parts.selected", { count: selection.length })}</div>
              <div className="flex flex-wrap items-center gap-1">
                <span className="w-20">{t("parts.move")}</span>
                {(
                  [
                    ["↑", 0, 1, 0, "north"],
                    ["↓", 0, -1, 0, "south"],
                    ["←", -1, 0, 0, "west"],
                    ["→", 1, 0, 0, "east"],
                    ["⤒", 0, 0, 1, "up"],
                    ["⤓", 0, 0, -1, "down"],
                  ] as const
                ).map(([icon, e, n, u, dir]) => (
                  <button
                    key={dir}
                    type="button"
                    className={smallButton}
                    disabled={busy}
                    title={t(`parts.dir.${dir}`)}
                    onClick={() => move(e * step, n * step, u * step)}
                    data-testid={`part-move-${dir}`}
                  >
                    {icon}
                  </button>
                ))}
                <select
                  className="rounded border border-zinc-300 bg-white px-1 py-0.5 dark:border-zinc-600 dark:bg-zinc-800"
                  value={step}
                  onChange={(e) => setStep(Number(e.target.value))}
                  aria-label={t("parts.step")}
                >
                  {[0.2, 0.5, 1, 2, 5].map((v) => (
                    <option key={v} value={v}>
                      {v} m
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <span className="w-20">{t("parts.turn")}</span>
                {[-90, -15, 15, 90].map((d) => (
                  <button
                    key={d}
                    type="button"
                    className={smallButton}
                    disabled={busy}
                    onClick={() => change({ turn_deg: d })}
                    data-testid={`part-turn-${d}`}
                  >
                    {d < 0 ? "⟲" : "⟳"} {Math.abs(d)}°
                  </button>
                ))}
              </div>
              {(
                [
                  ["height", (f: number) => [1, 1, f]],
                  ["width", (f: number) => [f, f, 1]],
                  ["whole", (f: number) => [f, f, f]],
                ] as const
              ).map(([kind, scale]) => (
                <div key={kind} className="flex flex-wrap items-center gap-1">
                  <span className="w-20">{t(`parts.${kind}`)}</span>
                  {[0.8, 0.9, 1.1, 1.25].map((f) => (
                    <button
                      key={f}
                      type="button"
                      className={smallButton}
                      disabled={busy}
                      onClick={() => change({ scale: scale(f) as [number, number, number] })}
                      data-testid={`part-${kind}-${Math.round((f - 1) * 100)}`}
                    >
                      {f < 1 ? "−" : "+"}
                      {Math.round(Math.abs(f - 1) * 100)} %
                    </button>
                  ))}
                </div>
              ))}
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  change({ hide: true });
                  setSelection([]);
                }}
                data-testid="part-hide"
              >
                🚫 {t("parts.hide")}
              </Button>
              <p className="text-zinc-500">{t("parts.ai_hint")}</p>
            </div>
          )}
        </>
      )}
      {ops > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-zinc-500">{t("parts.changed", { count: ops })}</span>
          <button
            type="button"
            className="underline"
            disabled={busy}
            onClick={() => void run(() => api.undoParts(jobId, object.item_id))}
            data-testid="parts-undo"
          >
            ↺ {t("parts.undo")}
          </button>
          <button
            type="button"
            className="underline"
            disabled={busy}
            onClick={() => void run(() => api.undoParts(jobId, object.item_id, true))}
            data-testid="parts-reset"
          >
            {t("parts.reset")}
          </button>
        </div>
      )}
      {error && <Alert kind="error">{error}</Alert>}
    </div>
  );
}

/** Which AI answers this request – the job's model by default; another one (e.g. a
 * stronger) when the first did not manage. Models without a key cannot be chosen. */
function ModelSelect({
  value,
  onChange,
  disabled,
  testId,
}: {
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
  testId: string;
}) {
  const { t } = useT();
  const system = useApp((s) => s.system);
  const [models] = useAiModels();
  return (
    <label className="flex items-center gap-1.5 text-xs">
      <span className="shrink-0 text-zinc-600 dark:text-zinc-300">{t("design.model")}</span>
      <select
        className="min-w-0 flex-1 rounded border border-zinc-300 bg-white px-1 py-0.5 dark:border-zinc-600 dark:bg-zinc-800"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      >
        {modelGroups(models, value).map((g) =>
          g.models.length ? (
            <optgroup key={g.provider} label={t(`provider.${g.provider}`)}>
              {g.models.map((m) => (
                <option key={m.id} value={m.id} disabled={!hasKeyFor(m.id, system)}>
                  {modelLabel(t, m)}
                  {!hasKeyFor(m.id, system) ? ` (${t("brief.no_key")})` : ""}
                </option>
              ))}
            </optgroup>
          ) : null,
        )}
      </select>
    </label>
  );
}

/** The best model with a key that is not the one that just failed (or null). */
function useStrongerModel(current: string): string | null {
  const system = useApp((s) => s.system);
  const [models] = useAiModels();
  const rank = { top: 0, mid: 1, low: 2 } as Record<string, number>;
  const now = models.find((m) => m.id === current);
  const better = models
    .filter((m) => m.id !== current && hasKeyFor(m.id, system) && !m.note)
    .filter((m) => !now?.tier || rank[m.tier ?? "low"] < rank[now.tier])
    .sort((a, b) => rank[a.tier ?? "low"] - rank[b.tier ?? "low"]);
  return better[0]?.id ?? null;
}

/** After a failed answer: offer to ask a stronger model (cost is shown again first). */
function TryStronger({ current, onPick, disabled }: { current: string; onPick: (model: string) => void; disabled?: boolean }) {
  const { t } = useT();
  const stronger = useStrongerModel(current);
  if (!stronger) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="design-try-stronger">
      <span className="text-zinc-600 dark:text-zinc-300">{t("design.try_stronger", { model: modelName(t, current) })}</span>
      <Button onClick={() => onPick(stronger)} disabled={disabled}>
        {t("design.try_with", { model: modelName(t, stronger) })}
      </Button>
    </div>
  );
}

/** Request to the AI architect in own words: cost first, then send, then keep or reset. */
function DesignPanel({
  jobId,
  object,
  onAdjusted,
  parts = [],
  partNames = [],
}: {
  jobId: string;
  object: PreviewObject;
  onAdjusted: (o: PreviewObject) => Promise<void>;
  parts?: string[]; // only these building parts are to change
  partNames?: string[];
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
  const [model, setModel] = useState(job?.brief?.ai_model ?? "");
  const [failed, setFailed] = useState(false);
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
  const check = (m = model) =>
    run(async () => {
      const e = await api.designEstimate(jobId, object.item_id, m);
      setEstimate({ typical: e.estimate_usd, max: Math.min(e.max_usd, e.budget_usd), budget: e.budget_usd, model: e.model });
    });
  const send = () =>
    run(async () => {
      if (!estimate) return;
      const events = useApp.getState().events;
      setSending({ since: events.length ? events[events.length - 1].seq : 0, started: Date.now() });
      setFailed(false);
      setDone(null);
      const out = await api
        .redesign(jobId, object.item_id, text.trim(), estimate.max, estimate.model, parts)
        .catch((e) => {
          setFailed(true);
          throw e;
        })
        .finally(() => setSending(null));
      setEstimate(null);
      setText("");
      setDone(t("design.done", { summary: out.summary, cost: out.cost_usd.toFixed(3), model: modelName(t, out.model) }));
      await showNew(out.job);
    });
  const pickModel = (m: string) => {
    setModel(m);
    setEstimate(null);
    setFailed(false);
    if (text.trim().length >= 3) void check(m);
  };
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
          {parts.length > 0 && (
            <p className="text-xs font-medium text-orange-600" data-testid="design-parts">
              {t("parts.ai_only", { names: partNames.join(", ") })}
            </p>
          )}
          <ModelSelect value={model} onChange={pickModel} disabled={busy} testId="design-model" />
          {!estimate ? (
            <Button onClick={() => void check()} disabled={busy || text.trim().length < 3} data-testid="design-check">
              {t("design.check")}
            </Button>
          ) : (
            <div className="space-y-1.5">
              <p className="text-xs text-zinc-600 dark:text-zinc-300">
                {t("design.estimate", {
                  typical: estimate.typical.toFixed(2),
                  max: estimate.max.toFixed(2),
                  budget: estimate.budget.toFixed(2),
                  model: modelName(t, estimate.model),
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
      {failed && !busy && <TryStronger current={model} onPick={pickModel} />}
    </div>
  );
}

/** A photo-realistic picture of the built scenery, rendered by the user's Blender
 * (Cycles: real sunlight, soft sky light, cast shadows) from the preview's view. */
function BlenderPhoto({
  jobId,
  itemId,
  view,
}: {
  jobId: string;
  itemId: string | null;
  view: () => { azimuth: number; elevation: number };
}) {
  const { t } = useT();
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  const render = async () => {
    setBusy(true);
    setErr(null);
    try {
      setUrl(await renderPhotoUrl(jobId, { item_id: itemId, ...view(), samples: 64 }));
    } catch (e) {
      setErr(e instanceof ApiError ? t(e.key, e.params) : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2 rounded border border-zinc-200 p-2 dark:border-zinc-700" data-testid="blender-photo">
      <p className="text-xs text-zinc-500">{t(itemId ? "photo.hint_item" : "photo.hint_scene")}</p>
      <Button variant="secondary" onClick={render} disabled={busy} data-testid="blender-photo-render">
        {busy ? <Spinner /> : "📷"} {t(busy ? "photo.rendering" : "photo.render")}
      </Button>
      {err && <Alert kind="error">{err}</Alert>}
      {url && (
        <a href={url} download="xpsb-photo.png" title={t("photo.save")}>
          <img src={url} alt={t("photo.alt")} className="w-full rounded" data-testid="blender-photo-image" />
        </a>
      )}
    </div>
  );
}
