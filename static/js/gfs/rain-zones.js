import { buildCellRing, normalizePolygonFeature, normalizePolygonFieldPayload } from './polygon_math.js';
import { ringRadToPath } from './polygon_render.js';

function polygonApiPath() {
  return 'gmp-polygon-3d.path';
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

function makePatch({ lat, lon, height, opacity }) {
  const el = document.createElement('gmp-polygon-3d');
  const feature = normalizePolygonFeature({ lat, lon, altitude_m: 0, cell_size_deg: 0.05 });
  const ring = buildCellRing(feature);
  const path = ringRadToPath(ring);
  el.path = path;
  el.setAttribute('altitude-mode', 'relative-to-ground');
  el.setAttribute('fill-color', '#5be7ff');
  el.setAttribute('fill-opacity', String(opacity));
  el.setAttribute('stroke-width', '0');
  el.setAttribute('extruded', 'true');
  el.setAttribute('extruded-height', String(height));
  return el;
}

export function renderRainZones({ payload, map3DElement }) {
  const created = [];
  if (!map3DElement || !payload?.fields) return () => {};
  console.info('[gfs rain] polygon api', { api: polygonApiPath() });
  const bbox = bboxFromPayload(payload);
  if (!bbox) return () => {};

  const contractFeatures = normalizePolygonFieldPayload(payload?.polygon_field_v1 || null);
  if (contractFeatures.length) {
    const frag = document.createDocumentFragment();
    const count = Math.min(contractFeatures.length, 320);
    for (let i = 0; i < count; i += 1) {
      const f = contractFeatures[i];
      const rainIntensity = Math.max(0, f.precip_rate);
      if (rainIntensity <= 0.03) continue;
      const cloudBoost = Math.max(0, f.cloud_total) / 300;
      const opacity = Math.min(0.62, 0.12 + rainIntensity * 0.23 + cloudBoost);
      const height = 180 + Math.round(Math.min(1, rainIntensity) * 1100);
      const patch = makePatch({ lat: f.lat, lon: f.lon, height, opacity });
      frag.append(patch);
      created.push(patch);
    }
    map3DElement.append(frag);
    console.info('[gfs rain] rendered patches', { count });
    return () => { created.forEach((el) => { try { el.remove(); } catch (_) {} }); };
  }

  const prate = to2DGrid(payload.fields.prate);
  const cloud = to2DGrid(payload.fields.cloud_cover);
  if (!prate.length) return () => {};

  const ny = prate.length;
  const nx = Array.isArray(prate[0]) ? prate[0].length : 0;
  if (!ny || !nx) return () => {};

  const step = Math.max(1, Math.floor(Math.max(nx, ny) / 36));
  const frag = document.createDocumentFragment();
  let count = 0;
  for (let i = 0; i < ny; i += step) {
    for (let j = 0; j < nx; j += step) {
      const p = toNumber(prate[i]?.[j], 0);
      if (p <= 0.05) continue;
      const c = toNumber(cloud?.[i]?.[j], 0);
      const { lat, lon } = latLonFromIndex(i, j, ny, nx, bbox);
      const height = Math.min(1300, 150 + p * 400 + c * 2.5);
      const opacity = Math.min(0.62, 0.16 + p * 0.23 + c / 250);
      const patch = makePatch({ lat, lon, height, opacity });
      frag.append(patch);
      created.push(patch);
      count += 1;
      if (count >= 320) break;
    }
    if (count >= 320) break;
  }

  map3DElement.append(frag);
  console.info('[gfs rain] rendered patches', { count });

  return () => {
    created.forEach((el) => { try { el.remove(); } catch (_) {} });
  };
}
