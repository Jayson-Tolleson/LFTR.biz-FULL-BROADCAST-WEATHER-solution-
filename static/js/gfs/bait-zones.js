function toNumber(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function buildHexRing(lat, lon, dLat, dLon, altitude) {
  const points = [];
  for (let i = 0; i < 6; i += 1) {
    const a = (Math.PI * 2 * i) / 6;
    points.push({
      lat: lat + (Math.sin(a) * dLat),
      lng: lon + (Math.cos(a) * dLon),
      altitude,
    });
  }
  return points;
}

function normalizeHit(hit) {
  if (!hit || typeof hit !== 'object') return null;
  if (typeof hit.lat === 'number' && typeof hit.lon === 'number') {
    return {
      lat: hit.lat,
      lon: hit.lon,
      probability: toNumber(hit.probability ?? hit.score ?? hit.value, null),
    };
  }
  if (Array.isArray(hit.center) && hit.center.length >= 2) {
    return {
      lat: toNumber(hit.center[1]),
      lon: toNumber(hit.center[0]),
      probability: toNumber(hit.probability ?? hit.score ?? hit.value, null),
    };
  }
  if (Array.isArray(hit.coordinates) && hit.coordinates.length >= 2) {
    return {
      lat: toNumber(hit.coordinates[1]),
      lon: toNumber(hit.coordinates[0]),
      probability: toNumber(hit.probability ?? hit.score ?? hit.value, null),
    };
  }
  return null;
}

function hitsFromPayload(payload) {
  const out = [];
  const scoreHits = Array.isArray(payload?.bait_score) ? payload.bait_score : [];
  scoreHits.forEach((h) => {
    const normalized = normalizeHit(h);
    if (normalized) out.push(normalized);
  });

  if (out.length) return out;

  const boil = Array.isArray(payload?.boil_probability_polygons) ? payload.boil_probability_polygons : [];
  boil.forEach((poly) => {
    const normalized = normalizeHit(poly);
    if (normalized) out.push(normalized);
  });
  return out;
}

function setCoordinates(el, points) {
  const serialized = points.map((p) => `${p.lat},${p.lng},${p.altitude}`).join(' ');
  el.setAttribute('outer-coordinates', serialized);
  el.outerCoordinates = points;
}

function makeLayerElement({ hit, mult, fillColor, fillOpacity, extrudedHeight, microOffset, strokeWidth = 1 }) {
  const el = document.createElement('gmp-polygon-3d');
  const baseScale = 0.03;
  const probBoost = hit.probability == null ? 0 : Math.min(0.07, Math.max(0, hit.probability) * 0.06);
  const latRadius = (baseScale + probBoost) * mult;
  const lonRadius = latRadius / Math.max(Math.cos(hit.lat * Math.PI / 180), 0.2);
  const altitudeBase = microOffset;
  const points = buildHexRing(hit.lat, hit.lon, latRadius, lonRadius, altitudeBase);
  setCoordinates(el, points);

  el.setAttribute('altitude-mode', 'relative-to-ground');
  el.altitudeMode = 'RELATIVE_TO_GROUND';
  el.setAttribute('fill-color', fillColor);
  el.setAttribute('fill-opacity', String(fillOpacity));
  el.setAttribute('stroke-color', fillColor);
  el.setAttribute('stroke-opacity', String(fillOpacity));
  el.setAttribute('stroke-width', String(strokeWidth));
  el.setAttribute('extruded', 'true');
  el.extruded = true;
  el.setAttribute('draws-occluded-segments', 'false');
  el.setAttribute('altitude', String(altitudeBase));
  el.setAttribute('extruded-height', String(extrudedHeight + microOffset));
  el.extrudedHeight = extrudedHeight + microOffset;

  return el;
}

export function renderBaitZones({ payload, map3DElement }) {
  const hits = hitsFromPayload(payload);
  const created = [];

  if (!map3DElement || !hits.length) {
    return () => {};
  }

  const frag = document.createDocumentFragment();

  hits.forEach((hit) => {
    const core = makeLayerElement({
      hit,
      mult: 1,
      fillColor: '#FF8C00',
      fillOpacity: 0.9,
      extrudedHeight: 100,
      microOffset: 0.1,
      strokeWidth: 1,
    });
    const inner = makeLayerElement({
      hit,
      mult: 1.5,
      fillColor: '#FFA500',
      fillOpacity: 0.5,
      extrudedHeight: 60,
      microOffset: 0.2,
      strokeWidth: 0,
    });
    const outer = makeLayerElement({
      hit,
      mult: 3,
      fillColor: '#FFD700',
      fillOpacity: 0.2,
      extrudedHeight: 30,
      microOffset: 0.3,
      strokeWidth: 0,
    });

    frag.append(core, inner, outer);
    created.push(core, inner, outer);
  });

  map3DElement.append(frag);

  return () => {
    created.forEach((el) => {
      try { el.remove(); } catch (_) {}
    });
  };
}
