/** Same local equirectangular approximation as the backend (xpsb/geo.py). */
const METERS_PER_DEG_LAT = (Math.PI * 6_371_008.8) / 180;
export const MAX_AOI_AREA_KM2 = 25;

export function ringAreaKm2(ring: number[][]): number {
  if (ring.length < 4) return 0;
  const pts = ring.slice(0, -1);
  const lat0 = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const lon0 = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const mx = METERS_PER_DEG_LAT * Math.cos((lat0 * Math.PI) / 180);
  const xy = ring.map(([lon, lat]) => [(lon - lon0) * mx, (lat - lat0) * METERS_PER_DEG_LAT]);
  let s = 0;
  for (let i = 0; i < xy.length - 1; i++) s += xy[i][0] * xy[i + 1][1] - xy[i + 1][0] * xy[i][1];
  return Math.abs(s) / 2 / 1e6;
}
