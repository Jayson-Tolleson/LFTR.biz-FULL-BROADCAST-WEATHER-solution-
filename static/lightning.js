(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function polylineSupported(state) {
    return !!(state?.maps3dLib && (state.maps3dLib.Polyline3DElement || state.maps3dLib.Polyline3DInteractiveElement));
  }

  function boltPoints(ctx, tile, branch = 0) {
    const lat = Number(tile.bounds?.lat_center || 0);
    const lon = Number(tile.bounds?.lon_center || 0);
    const top = Number(tile.top_altitude_m || tile.bands?.high?.top_altitude_m || 9000);
    const bottom = Math.max(20, Number(tile.base_altitude_m || tile.bands?.low?.base_altitude_m || 1400) * 0.3);
    const pts = [];
    const segs = 7;
    for (let i = 0; i <= segs; i += 1) {
      const t = i / segs;
      const jitterLat = ctx.seededRange(`${tile.tile_id}:bolt:${branch}:${i}:lat`, -0.06, 0.06) * (1 - t * 0.65);
      const jitterLon = ctx.seededRange(`${tile.tile_id}:bolt:${branch}:${i}:lon`, -0.07, 0.07) * (1 - t * 0.65);
      pts.push({ lat: lat + jitterLat, lng: lon + jitterLon, altitude: top - (top - bottom) * t });
    }
    return pts;
  }

  function addBolt(ctx, tile, branch = 0) {
    const { state } = ctx;
    const pts = boltPoints(ctx, tile, branch);
    if (polylineSupported(state)) {
      const Ctor = state.maps3dLib.Polyline3DElement || state.maps3dLib.Polyline3DInteractiveElement;
      try {
        const p = new Ctor({
          altitudeMode: state.maps3dLib.AltitudeMode.ABSOLUTE,
          strokeColor: 'rgba(255,248,170,0.9)',
          strokeWidth: 2.1,
          geodesic: true,
          drawsOccludedSegments: true,
          path: pts,
        });
        ctx.globeEl.append(p);
        state.lightningPolygons.push(p);
        return;
      } catch (e) {
        console.warn('[gfs] polyline bolt fallback engaged', e);
      }
    }
    const path = ctx.buildAltitudePath(pts.map((k) => ({ lat: k.lat, lng: k.lng })), Number(tile.bands?.high?.base_altitude_m || 6000));
    ctx.appendSimplePolygon(path, 'rgba(255,242,160,0.20)', 'rgba(255,232,120,0.46)', 94, state.lightningPolygons, false, '', 'elevated', false);
  }

  function renderHailAndLightning(ctx, lod) {
    const { state } = ctx;
    ctx.clearHailAndLightning();
    const severe = state.cloudTiles
      .filter((t) => Number(t.storm_energy ?? (t.convection_factor || 0)) > 0.52 && Number(t.precip_rate ?? ((t.precipitation_factor || 0) * 45)) > 6)
      .slice(0, Math.min(95, lod.tileLimit));

    for (const tile of severe) {
      const lat = Number(tile.bounds?.lat_center || 0);
      const lon = Number(tile.bounds?.lon_center || 0);
      const key = `${tile.tile_id || 'tile'}:${tile.seed || tile.updated_at || 0}`;
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;

      const hailRing = ctx.buildStormRing(lat, lon, ctx.seededRange(`${key}:hs`, 0.03, 0.08), `${key}:hail`);
      ctx.appendSimplePolygon(ctx.buildAltitudePath(hailRing, Number(tile.bands?.mid?.base_altitude_m || 2400)), 'rgba(216,252,255,0.20)', 'rgba(238,255,255,0.35)', 80, state.hailCorePolygons, Number(tile.importance || 0) > 0.75, `Hail core ${tile.tile_id || ''}`, 'elevated', true);

      if (ctx.seededRange(`${key}:flash`, 0, 1) > 0.45) {
        addBolt(ctx, tile, 0);
        if (ctx.seededRange(`${key}:branch`, 0, 1) > 0.62) addBolt(ctx, tile, 1);
      }

      const hailDrops = Math.max(8, Math.min(26, Math.round(10 + Number(tile.storm_energy || tile.convection_factor || 0) * 20)));
      for (let i = 0; i < hailDrops; i += 1) {
        const pLat = lat + ctx.seededRange(`${key}:hp:${i}:lat`, -0.08, 0.08);
        const pLon = lon + ctx.seededRange(`${key}:hp:${i}:lon`, -0.08, 0.08);
        const alt = Number(tile.top_altitude_m || tile.bands?.high?.top_altitude_m || 9000) * ctx.seededRange(`${key}:hp:${i}:a`, 0.16, 0.96);
        const pin = ctx.buildRainSpherePin(state.markerLib, { precip: 0.42, scale: 0.36, opacity: 0.93 });
        const marker = new state.maps3dLib.Marker3DInteractiveElement({ position: { lat: ctx.clampLat(pLat), lng: ctx.wrapLon(pLon), altitude: alt } });
        marker.append(pin);
        ctx.globeEl.append(marker);
        state.hailMarkers.push(marker);
      }
    }
  }

  NS.renderHailAndLightning = renderHailAndLightning;
})();
