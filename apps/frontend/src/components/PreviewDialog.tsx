import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { imageryObjectUrl, preview, type PreviewData, type PreviewObject } from "../api/client";
import { useT } from "../i18n";
import { CATEGORY_COLORS, categoryLabelKey } from "../scene";
import { Alert, Button, Spinner } from "./ui";

// Local metres (x east, y north, z up) → three.js (y up, -z north).
const toThree = (x: number, y: number, z: number) => new THREE.Vector3(x, z, -y);

/** Interactive 3D view of everything the plan will build, before anything is written. */
export function PreviewDialog({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const { t } = useT();
  const mount = useRef<HTMLDivElement>(null);
  const focusRef = useRef<(o: PreviewObject) => void>(() => {});
  const [data, setData] = useState<PreviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
        const loader = new THREE.TextureLoader();
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
        for (const o of d.objects.filter((x) => x.status === "ok")) {
          const [m, texUrl] = await Promise.all([preview.mesh(jobId, o.stem), preview.textureUrl(jobId, o.stem)]);
          if (disposed) return;
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
          scene.add(mesh);
          box.expandByObject(mesh);
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
      controls.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          const mat = obj.material as THREE.MeshStandardMaterial;
          mat.map?.dispose();
          mat.dispose();
        }
      });
      renderer.dispose();
      el.removeChild(renderer.domElement);
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [jobId]);

  const problems = data?.objects.filter((o) => o.status !== "ok") ?? [];
  const built = data?.objects.filter((o) => o.status === "ok") ?? [];

  return (
    <div className="fixed inset-0 z-50 flex bg-black/60 p-4" role="dialog" aria-modal="true" data-testid="preview-dialog">
      <div className="flex h-full w-full overflow-hidden rounded-lg bg-white shadow-xl dark:bg-zinc-900">
        <aside className="flex w-72 shrink-0 flex-col border-r border-zinc-200 dark:border-zinc-700">
          <div className="flex items-center justify-between border-b border-zinc-200 px-3 py-2 dark:border-zinc-700">
            <h2 className="text-sm font-semibold">{t("preview.title")}</h2>
            <Button variant="ghost" onClick={onClose} data-testid="preview-close">
              {t("common.close")}
            </Button>
          </div>
          <div className="flex-1 space-y-3 overflow-y-auto p-3 text-sm">
            <p className="text-xs text-zinc-500">{t("preview.hint")}</p>
            {loading && (
              <div className="flex items-center gap-2">
                <Spinner /> {t("preview.loading")}
              </div>
            )}
            {error && <Alert kind="error">{error}</Alert>}
            {built.length > 0 && (
              <ul className="space-y-0.5" data-testid="preview-objects">
                {built.map((o) => (
                  <li key={o.stem}>
                    <button className="w-full truncate text-left hover:underline" onClick={() => focusRef.current(o)}>
                      {o.name || t(categoryLabelKey({ kind: "object", category: o.category as never }))}
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
                      {o.name || t(categoryLabelKey({ kind: "object", category: o.category as never }))}:{" "}
                      {t(`preview.status.${o.status}`)}
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
