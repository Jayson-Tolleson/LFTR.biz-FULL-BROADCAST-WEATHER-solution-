(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function polylineSupported(state) {
    return !!(state?.maps3dLib && (state.maps3dLib.Polyline3DElement || state.maps3dLib.Polyline3DInteractiveElement));
  }

  function boltPath(ctx, event, branch = 0) {
    const lat = Number(event.lat || 0);
    const lon = Number(event.lon || 0);
    const top = Number(event.estimated_flash_top_m || 9000);
    const bottom = Number(event.estimated_flash_bottom_m || 700);
    const pts = [];
    const segs = 8;
    for (let i = 0; i <= segs; i += 1) {
      const t = i / segs;
      const jitterLat = ctx.seededRange(`${event.tile_id}:b:${branch}:${i}:lat`, -0.06, 0.06) * (1 - t * 0.68);
      const jitterLon = ctx.seededRange(`${event.tile_id}:b:${branch}:${i}:lon`, -0.07, 0.07) * (1 - t * 0.68);
      pts.push({ lat: lat + jitterLat, lng: lon + jitterLon, altitude: top - (top - bottom) * t });
    }
    return pts;
  }

  function removeRegistryEntry(entry) {
    (entry?.objects || []).forEach((obj) => { try { obj.remove(); } catch (_) {} });
  }

  function addBolt(ctx, registryEntry, event, energy, branch = 0) {
    const { state } = ctx;
    const pts = boltPath(ctx, event, branch);
    if (polylineSupported(state)) {
      const Ctor = state.maps3dLib.Polyline3DElement || state.maps3dLib.Polyline3DInteractiveElement;
      try {
        const p = new Ctor({
          altitudeMode: state.maps3dLib.AltitudeMode.ABSOLUTE,
          strokeColor: `rgba(255,248,170,${Math.max(0.4, Math.min(0.95, 0.45 + energy * 0.5)).toFixed(3)})`,
          strokeWidth: 1.3 + energy * 1.4,
          geodesic: true,
          drawsOccludedSegments: true,
          path: pts,
        });
        ctx.globeEl.append(p);
        registryEntry.objects.push(p);
        state.lightningPolygons.push(p);
        return;
      } catch (e) {
        console.warn('[gfs] polyline lightning fallback', e);
      }
    }

    const path = ctx.buildAltitudePath(pts.map((k) => ({ lat: k.lat, lng: k.lng })), Number(event.estimated_flash_top_m || 7800));
    const poly = ctx.appendSimplePolygon(path, 'rgba(255,246,168,0.14)', 'rgba(255,232,120,0.52)', 96, state.lightningPolygons, false, '', 'elevated', false);
    if (poly) registryEntry.objects.push(poly);
  }

  function lightningKey(ev, nowBucket) {
    const lat = Number(ev.lat || 0).toFixed(2);
    const lon = Number(ev.lon || 0).toFixed(2);
    const t = String(ev.time_bucket || ev.valid_time || nowBucket);
    const e = Number(ev.estimated_energy || ev.intensity || 0).toFixed(2);
    const k = String(ev.type || ev.kind || 'strike');
    return String(ev.dedup_key || `${lat}:${lon}:${t}:${e}:${k}`);
  }

  function renderHailAndLightning(ctx, lod) {
    const { state } = ctx;
    if (!state.feature.lightning || !state.mapReady || !state.maps3dLib || !state.markerLib) return;

    const registry = state.lightningRegistry || new Map();
    state.lightningRegistry = registry;
    const keep = new Set();

    const events = (state.weatherPayload?.lightning_events || []).slice(0, Math.min(120, lod.tileLimit));
    const fallback = state.cloudTiles
      .filter((t) => Number(t.storm_energy ?? (t.convection_factor || 0)) > 0.52 && Number(t.precip_rate ?? ((t.precipitation_factor || 0) * 45)) > 6)
      .slice(0, Math.min(95, lod.tileLimit))
      .map((t) => ({
        tile_id: t.tile_id || 'tile',
        lat: Number(t.bounds?.lat_center || 0),
        lon: Number(t.bounds?.lon_center || 0),
        estimated_flash_top_m: Number(t.top_altitude_m || t.bands?.high?.top_altitude_m || 9000),
        estimated_flash_bottom_m: Number(t.base_altitude_m || t.bands?.low?.base_altitude_m || 1500) * 0.32,
        estimated_energy: Number(t.storm_energy || t.convection_factor || 0),
      }));

    const severe = (events.length ? events : fallback).filter((e) => Number.isFinite(e.lat) && Number.isFinite(e.lon));
    const nowBucket = Math.floor(Date.now() / 3200);
    let boltBudget = 0;

    for (const ev of severe) {
      if (boltBudget >= Math.max(24, Math.min(90, lod.tileLimit))) break;
      const key = lightningKey(ev, nowBucket);
      const energy = Math.max(0, Math.min(1, Number(ev.estimated_energy || 0.5)));
      const shouldFlash = ctx.seededRange(`${key}:flash`, 0, 1) > (0.56 - energy * 0.24);
      if (!shouldFlash) continue;
      keep.add(key);
      if (registry.has(key)) {
        registry.get(key).lastSeen = nowBucket;
        continue;
      }

      const entry = { objects: [], lastSeen: nowBucket };
      registry.set(key, entry);

      const hailRing = ctx.buildStormRing(ev.lat, ev.lon, ctx.seededRange(`${key}:hs`, 0.03, 0.09), `${key}:hail`);
      const hailPoly = ctx.appendSimplePolygon(
        ctx.buildAltitudePath(hailRing, Math.max(1200, Number(ev.estimated_flash_bottom_m || 1400))),
        'rgba(216,252,255,0.18)',
        'rgba(238,255,255,0.34)',
        82,
        state.hailCorePolygons,
        false,
        '',
        'elevated',
        true,
      );
      if (hailPoly) entry.objects.push(hailPoly);

      addBolt(ctx, entry, ev, energy, 0);
      boltBudget += 1;
      if (ctx.seededRange(`${key}:branch`, 0, 1) > (0.68 - energy * 0.22)) {
        addBolt(ctx, entry, ev, energy, 1);
        boltBudget += 1;
      }

      const hailDrops = Math.max(4, Math.min(18, Math.round(5 + energy * 14)));
      for (let i = 0; i < hailDrops; i += 1) {
        const pLat = ev.lat + ctx.seededRange(`${key}:hp:${i}:lat`, -0.07, 0.07);
        const pLon = ev.lon + ctx.seededRange(`${key}:hp:${i}:lon`, -0.07, 0.07);
        const alt = Number(ev.estimated_flash_top_m || 8800) * ctx.seededRange(`${key}:hp:${i}:a`, 0.24, 0.92);
        const pin = ctx.buildRainSpherePin(state.markerLib, { precip: 0.42, scale: 0.33, opacity: 0.92, colorOverride: 'rgba(217,243,255,0.92)' });
        const marker = new state.maps3dLib.Marker3DInteractiveElement({ position: { lat: ctx.clampLat(pLat), lng: ctx.wrapLon(pLon), altitude: alt } });
        marker.append(pin);
        ctx.globeEl.append(marker);
        entry.objects.push(marker);
        state.hailMarkers.push(marker);
      }
    }

    for (const [key, entry] of [...registry.entries()]) {
      if (!keep.has(key)) {
        removeRegistryEntry(entry);
        registry.delete(key);
      }
    }
  }

  NS.renderHailAndLightning = renderHailAndLightning;
})();
