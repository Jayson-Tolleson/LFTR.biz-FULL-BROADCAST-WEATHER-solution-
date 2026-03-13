import { normalizePolygonFieldPayload } from './polygon_math.js';
import { clamp01 } from './greek_math.js';
import { createPolygon3D } from './polygon3d.js';

function polygonApiPath() {
  return window.google?.maps?.maps3d?.Polygon3DElement ? 'Polygon3DElement.path' : 'gmp-polygon-3d.path';
}

function toNumber(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function clampProbability(value) {
  return clamp01(toNumber(value, 0));
}

function toHexByte(value) {
  const clamped = Math.max(0, Math.min(255, Math.round(value)));
  return clamped.toString(16).padStart(2, '0');
}

function rgbHex(r, g, b) {
  return `#${toHexByte(r)}${toHexByte(g)}${toHexByte(b)}`;
}

function interpolateColor(a, b, t) {
  const p = clampProbability(t);
  return {
    r: a.r + ((b.r - a.r) * p),
    g: a.g + ((b.g - a.g) * p),
    b: a.b + ((b.b - a.b) * p),
  };
}

function probabilityBaseColor(probability) {
  const p = clampProbability(probability);
  const red = { r: 255, g: 32, b: 32 };
  const yellow = { r: 255, g: 232, b: 64 };
  const green = { r: 64, g: 214, b: 92 };
  return p < 0.5
    ? interpolateColor(red, yellow, p / 0.5)
    : interpolateColor(yellow, green, (p - 0.5) / 0.5);
}

function probabilityColorRamp(probability) {
  const base = probabilityBaseColor(probability);
  const toLayerHex = (mix) => rgbHex(
    base.r + ((255 - base.r) * mix),
    base.g + ((255 - base.g) * mix),
    base.b + ((255 - base.b) * mix),
  );
  return {
    coreColor: toLayerHex(0),
    innerColor: toLayerHex(0.2),
    outerColor: toLayerHex(0.4),
  };
}

function toPath(coords, altitude = 20) {
  if (!Array.isArray(coords)) return [];
  return coords
    .filter((p) => Array.isArray(p) && p.length >= 2)
    .map((p) => ({ lat: toNumber(p[1]), lng: toNumber(p[0]), altitude }));
}

function makePolygonLayer(path, fillColor, fillOpacity, extrudedHeight) {
  return createPolygon3D({
    path,
    altitude: 20,
    altitudeMode: 'relative',
    fillColor,
    fillOpacity,
    strokeColor: fillColor,
    strokeOpacity: fillOpacity,
    strokeWidth: 0.8,
    extrudedHeight,
  });
}

function makeLineOverlay(line) {
  const el = document.createElement('gmp-polyline-3d');
  const pts = Array.isArray(line?.coordinates) ? line.coordinates : [];
  const path = pts
    .filter((p) => Array.isArray(p) && p.length >= 2)
    .map((p) => ({ lat: toNumber(p[1]), lng: toNumber(p[0]), altitude: 15 }));
  if (path.length < 2) return null;
  el.path = path;
  el.setAttribute('altitude-mode', 'relative-to-ground');
  el.setAttribute('stroke-color', '#ffe38d');
  el.setAttribute('stroke-width', '2');
  el.setAttribute('stroke-opacity', '0.85');
  return el;
}

export function renderBaitZones({ payload, map3DElement }) {
  const created = [];
  if (!map3DElement || !payload) return () => {};
  console.info('[gfs bait] polygon api', { api: polygonApiPath() });

  const bait = payload?.bait || {};
  const legacyPolygonField = payload?.polygon_field_v1;
  if (legacyPolygonField) {
    normalizePolygonFieldPayload(legacyPolygonField);
  }
  if (bait.status !== 'ready' || bait.source !== 'full_stack') {
    console.info('[gfs bait] suppressed render', { status: bait.status, source: bait.source });
    return () => {};
  }

  const polygons = Array.isArray(bait.polygons) ? bait.polygons : [];
  const frag = document.createDocumentFragment();

  polygons.forEach((poly) => {
    const path = toPath(poly?.coordinates, 20);
    if (path.length < 3) return;
    const p = clampProbability(poly?.probability);
    const { coreColor, innerColor, outerColor } = probabilityColorRamp(p);
    const core = makePolygonLayer(path, coreColor, 0.84, 90);
    const inner = makePolygonLayer(path, innerColor, 0.46, 62);
    const outer = makePolygonLayer(path, outerColor, 0.2, 36);
    frag.append(core, inner, outer);
    created.push(core, inner, outer);
  });

  const lines = Array.isArray(payload?.front_lines) ? payload.front_lines : [];
  lines.forEach((line) => {
    const el = makeLineOverlay(line);
    if (!el) return;
    frag.append(el);
    created.push(el);
  });

  if (!created.length) return () => {};
  map3DElement.append(frag);
  console.info('[gfs bait] rendered full-stack polygons', { polygons: polygons.length, lines: lines.length });

  return () => {
    created.forEach((el) => {
      try { el.remove(); } catch (_) {}
    });
  };
}
