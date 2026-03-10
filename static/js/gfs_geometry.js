export function validatePath(path, counters) {
  if (!Array.isArray(path) || path.length < 3) {
    counters.empty_path = (counters.empty_path || 0) + 1;
    return null;
  }
  const out = [];
  for (const p of path) {
    const lat = Number(p.lat), lng = Number(p.lng), alt = Math.max(0, Math.min(18000, Number(p.alt ?? 0)));
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || !Number.isFinite(alt)) {
      counters.dropped_nonfinite = (counters.dropped_nonfinite || 0) + 1;
      continue;
    }
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) {
      counters.invalid_path = (counters.invalid_path || 0) + 1;
      continue;
    }
    out.push({ lat, lng, alt });
  }
  if (out.length < 3) {
    counters.invalid_path = (counters.invalid_path || 0) + 1;
    return null;
  }
  return out;
}
