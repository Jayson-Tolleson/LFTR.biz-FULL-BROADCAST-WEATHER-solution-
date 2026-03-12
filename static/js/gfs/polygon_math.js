import { bearingFromUV, cellOffsets, clamp01, degToRad, wrappedLongitude } from './greek_math.js';

function num(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function finiteOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function hasBelievableOceanSignal({ sst, chlorophyll, current_u, current_v, optional_ssh_anomaly }) {
  if (sst != null && sst >= -2.5 && sst <= 38.5) return true;
  if (chlorophyll != null && chlorophyll > 0) return true;
  if (current_u != null && current_v != null && Math.hypot(current_u, current_v) >= 0.03) return true;
  if (optional_ssh_anomaly != null && Math.abs(optional_ssh_anomaly) >= 0.01) return true;
  return false;
}

export function normalizePolygonFeature(feature) {
  const safe = feature && typeof feature === 'object' ? feature : {};
  return {
    lat: num(safe.lat ?? safe.latitude, 0),
    lon: num(safe.lon ?? safe.lng ?? safe.longitude, 0),
    altitude_m: num(safe.altitude_m ?? safe.altitude ?? 0, 0),
    confidence: clamp01(num(safe.confidence ?? safe.probability ?? safe.score ?? 0, 0)),
    cell_size_deg: num(safe.cell_size_deg ?? safe.cellSizeDeg ?? 0.1, 0.1),
    wind_u: num(safe.wind_u ?? safe.windU ?? 0, 0),
    wind_v: num(safe.wind_v ?? safe.windV ?? 0, 0),
    precip_rate: num(safe.precip_rate ?? 0, 0),
    cloud_total: num(safe.cloud_total ?? 0, 0),
    cloud_low: num(safe.cloud_low ?? 0, 0),
    cloud_mid: num(safe.cloud_mid ?? 0, 0),
    cloud_high: num(safe.cloud_high ?? 0, 0),
    air_temp: num(safe.air_temp ?? 0, 0),
    rel_humidity: num(safe.rel_humidity ?? 0, 0),
    dewpoint: num(safe.dewpoint ?? 0, 0),
    pressure_msl: num(safe.pressure_msl ?? 0, 0),
    rings: Array.isArray(safe.rings) ? safe.rings : [],
  };
}

export function buildCellRing(feature) {
  const norm = normalizePolygonFeature(feature);
  const { φ, λ } = degToRad(norm.lat, norm.lon);
  const { Δφ, Δλ } = cellOffsets(norm.cell_size_deg);
  const h = norm.altitude_m;

  return [
    [φ - Δφ, wrappedLongitude(λ - Δλ), h],
    [φ - Δφ, wrappedLongitude(λ + Δλ), h],
    [φ + Δφ, wrappedLongitude(λ + Δλ), h],
    [φ + Δφ, wrappedLongitude(λ - Δλ), h],
    [φ - Δφ, wrappedLongitude(λ - Δλ), h],
  ];
}

export function buildOrientedCellRing(feature) {
  const norm = normalizePolygonFeature(feature);
  const θ = bearingFromUV(norm.wind_u, norm.wind_v);
  const β = 0.8 + norm.confidence * 0.6;
  const { φ, λ } = degToRad(norm.lat, norm.lon);
  const { Δφ, Δλ } = cellOffsets(norm.cell_size_deg);
  const h = norm.altitude_m;

  const a = Math.sin(θ) * Δφ * β;
  const b = Math.cos(θ) * Δλ * β;

  return [
    [φ - Δφ - a, wrappedLongitude(λ - Δλ - b), h],
    [φ - Δφ + a, wrappedLongitude(λ + Δλ - b), h],
    [φ + Δφ + a, wrappedLongitude(λ + Δλ + b), h],
    [φ + Δφ - a, wrappedLongitude(λ - Δλ + b), h],
    [φ - Δφ - a, wrappedLongitude(λ - Δλ - b), h],
  ];
}

export function buildRingSet(features) {
  if (!Array.isArray(features)) return [];
  return features.map((f) => buildCellRing(f));
}

export function normalizePolygonFieldPayload(payload) {
  const safe = payload && typeof payload === 'object' ? payload : {};
  if (safe.schema !== 'gfs_polygon_field_v1') return [];
  const fields = safe.fields && typeof safe.fields === 'object' ? safe.fields : {};
  const lat = Array.isArray(fields.lat) ? fields.lat : [];
  const lon = Array.isArray(fields.lon) ? fields.lon : [];
  const altitude = Array.isArray(fields.altitude_m) ? fields.altitude_m : [];
  const n = Math.min(lat.length, lon.length);
  const out = [];
  for (let i = 0; i < n; i += 1) {
    out.push(normalizePolygonFeature({
      lat: lat[i],
      lon: lon[i],
      altitude_m: altitude[i] ?? 0,
      cell_size_deg: safe.cell_size_deg ?? 0.1,
      wind_u: fields.wind_u?.[i] ?? 0,
      wind_v: fields.wind_v?.[i] ?? 0,
      air_temp: fields.air_temp?.[i] ?? 0,
      rel_humidity: fields.rel_humidity?.[i] ?? 0,
      dewpoint: fields.dewpoint?.[i] ?? 0,
      pressure_msl: fields.pressure_msl?.[i] ?? 0,
      precip_rate: fields.precip_rate?.[i] ?? 0,
      cloud_total: fields.cloud_total?.[i] ?? 0,
      cloud_low: fields.cloud_low?.[i] ?? 0,
      cloud_mid: fields.cloud_mid?.[i] ?? 0,
      cloud_high: fields.cloud_high?.[i] ?? 0,
      confidence: Math.min(1, Math.max(0, num(fields.cloud_total?.[i], 0) / 100)),
    }));
  }
  return out;
}


export function normalizeBaitBaseFieldPayload(payload) {
  const safe = payload && typeof payload === 'object' ? payload : {};
  if (safe.schema !== 'gfs_bait_field_v1') return [];
  return normalizePolygonFieldPayload({
    ...safe,
    schema: 'gfs_polygon_field_v1',
  });
}

export function normalizeBaitOceanFieldPayload(payload) {
  const safe = payload && typeof payload === 'object' ? payload : {};
  if (safe.schema !== 'bait_ocean_field_v1') return [];
  const fields = safe.fields && typeof safe.fields === 'object' ? safe.fields : {};
  const lat = Array.isArray(fields.lat) ? fields.lat : [];
  const lon = Array.isArray(fields.lon) ? fields.lon : [];
  const n = Math.min(lat.length, lon.length);
  const out = [];
  for (let i = 0; i < n; i += 1) {
    const latV = finiteOrNull(lat[i]);
    const lonV = finiteOrNull(lon[i]);
    if (latV == null || lonV == null) continue;
    if (latV < -90 || latV > 90 || lonV < -180 || lonV > 180) continue;

    const sst = finiteOrNull(fields.sst?.[i]);
    const chlorophyll = finiteOrNull(fields.chlorophyll?.[i]);
    const current_u = finiteOrNull(fields.current_u?.[i]);
    const current_v = finiteOrNull(fields.current_v?.[i]);
    const water_color_index = finiteOrNull(fields.water_color_index?.[i]);
    const optional_ssh_anomaly = finiteOrNull(fields.optional_ssh_anomaly?.[i]);

    if (!hasBelievableOceanSignal({ sst, chlorophyll, current_u, current_v, optional_ssh_anomaly })) continue;

    out.push({
      lat: latV,
      lon: lonV,
      sst: sst ?? 0,
      chlorophyll: chlorophyll ?? 0,
      current_u: current_u ?? 0,
      current_v: current_v ?? 0,
      water_color_index: water_color_index ?? 0,
      optional_ssh_anomaly: optional_ssh_anomaly ?? 0,
    });
  }
  return out;
}
