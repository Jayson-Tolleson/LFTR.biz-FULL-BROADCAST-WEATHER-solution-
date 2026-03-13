import { buildCellRing, normalizePolygonFeature, normalizePolygonFieldPayload } from './polygon_math.js';
import { ringRadToPath } from './polygon_render.js';
import { createPolygon3D } from './polygon3d.js';

function polygonApiPath() {
  return window.google?.maps?.maps3d?.Polygon3DElement ? 'Polygon3DElement.path' : 'gmp-polygon-3d.path';
}

function toNumber(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function to2DGrid(value) {
  if (!Array.isArray(value)) return [];
  if (!Array.isArray(value[0])) return [];
  if (Array.isArray(value[0][0])) return value[0];
  return value;
}

function bboxFromPayload(payload) {
  const box = Array.isArray(payload?.bbox) ? payload.bbox : null;
  if (!box || box.length < 4) return null;
  return { west: toNumber(box[0]), south: toNumber(box[1]), east: toNumber(box[2]), north: toNumber(box[3]) };
}

function latLonFromIndex(i, j, ny, nx, bbox) {
  const lat = bbox.south + ((i + 0.5) / Math.max(1, ny)) * (bbox.north - bbox.south);
  const lon = bbox.west + ((j + 0.5) / Math.max(1, nx)) * (bbox.east - bbox.west);
  return { lat, lon };
}

function cloudFeaturesFromContract(payload) {
  return normalizePolygonFieldPayload(payload?.polygon_field_v1 || null);
}

function makeColumn({ lat, lon, height, color, opacity }) {
  const feature = normalizePolygonFeature({ lat, lon, altitude_m: 0, cell_size_deg: 0.07 });
  const ring = buildCellRing(feature);
  const path = ringRadToPath(ring);
  return createPolygon3D({
    path,
    altitude: 0,
    altitudeMode: 'relative',
    fillColor: color,
    fillOpacity: opacity,
    strokeColor: color,
    strokeOpacity: 0,
    strokeWidth: 0,
    extrudedHeight: height,
  });
}

export function renderCloudZones({ payload, map3DElement }) {
  const created = [];
  if (!map3DElement || !payload) return () => {};
  console.info('[gfs clouds] polygon api', { api: polygonApiPath() });
  const bbox = bboxFromPayload(payload);
  if (!bbox) return () => {};

  const contractFeatures = cloudFeaturesFromContract(payload);
  if (contractFeatures.length) {
    const frag = document.createDocumentFragment();
    const count = Math.min(contractFeatures.length, 260);
    for (let i = 0; i < count; i += 1) {
      const f = contractFeatures[i];
      const cloudDensity = Math.max(f.cloud_low, f.cloud_total * 0.7);
      const confidence = Math.min(1, Math.max(0, cloudDensity / 100));
      const opacity = Math.min(0.58, 0.18 + confidence * 0.48);
      const height = 700 + Math.round(confidence * 1800);
      const col = makeColumn({ lat: f.lat, lon: f.lon, height, color: '#cfe8ff', opacity });
      frag.append(col);
      created.push(col);
    }
    map3DElement.append(frag);
    console.info('[gfs clouds] rendered columns', { count });
    return () => { created.forEach((el) => { try { el.remove(); } catch (_) {} }); };
  }

  const low = to2DGrid(payload?.cloud_layers?.find((l) => l?.name === 'low')?.density);
  const refl = to2DGrid(payload?.convective?.reflectivity);
  if (!low.length && !refl.length) return () => {};

  const grid = low.length ? low : refl;
  const ny = grid.length;
  const nx = Array.isArray(grid[0]) ? grid[0].length : 0;
  if (!ny || !nx) return () => {};

  const step = Math.max(1, Math.floor(Math.max(nx, ny) / 32));
  const frag = document.createDocumentFragment();
  let count = 0;

  for (let i = 0; i < ny; i += step) {
    for (let j = 0; j < nx; j += step) {
      const lowVal = toNumber(low?.[i]?.[j], 0);
      const reflVal = toNumber(refl?.[i]?.[j], 0);
      if (lowVal < 35 && reflVal < 12) continue;
      const { lat, lon } = latLonFromIndex(i, j, ny, nx, bbox);
      const height = Math.min(2400, 500 + lowVal * 18 + reflVal * 10);
      const opacity = Math.min(0.58, 0.16 + (lowVal / 200) + (reflVal / 150));
      const col = makeColumn({ lat, lon, height, color: '#cfe8ff', opacity });
      frag.append(col);
      created.push(col);
      count += 1;
      if (count >= 260) break;
    }
    if (count >= 260) break;
  }

  map3DElement.append(frag);
  console.info('[gfs clouds] rendered columns', { count });

  return () => {
    created.forEach((el) => { try { el.remove(); } catch (_) {} });
  };
}
