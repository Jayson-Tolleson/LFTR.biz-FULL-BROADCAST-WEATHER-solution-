import { radToDeg } from './greek_math.js';

export function ringRadToPath(ring) {
  if (!Array.isArray(ring)) return [];
  return ring.map((vertex) => {
    const phiRad = Number(vertex?.[0] ?? 0);
    const lambdaRad = Number(vertex?.[1] ?? 0);
    const altitude = Number(vertex?.[2] ?? 0);
    const { lat, lon } = radToDeg(phiRad, lambdaRad);
    return { lat, lng: lon, altitude };
  });
}

export function ringsRadToPaths(rings) {
  if (!Array.isArray(rings)) return [];
  return rings.map((ring) => ringRadToPath(ring));
}

export function pathToOuterCoordinates(path) {
  if (!Array.isArray(path)) return '';
  return path.map((p) => `${p.lat},${p.lng},${p.altitude ?? 0}`).join(' ');
}
