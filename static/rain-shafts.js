(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function seeded(ctx, key, min, max) {
    return ctx.seededRange ? ctx.seededRange(key, min, max) : (min + (max - min) * 0.5);
  }

  function precipColor(precipNorm, alpha) {
    const a = Math.max(0.08, Math.min(0.85, alpha));
    if (precipNorm > 0.92) return `rgba(36,36,36,${a.toFixed(3)})`;
    if (precipNorm > 0.8) return `rgba(198,34,34,${a.toFixed(3)})`;
    if (precipNorm > 0.64) return `rgba(239,128,33,${a.toFixed(3)})`;
    if (precipNorm > 0.5) return `rgba(242,216,62,${a.toFixed(3)})`;
    if (precipNorm > 0.34) return `rgba(82,192,96,${a.toFixed(3)})`;
    if (precipNorm > 0.18) return `rgba(79,156,242,${a.toFixed(3)})`;
    return `rgba(235,245,255,${a.toFixed(3)})`;
  }

  function columnFootprint(ctx, lat, lon, spread, key) {
    const pts = [];
    const n = 8;
    for (let i = 0; i < n; i += 1) {
      const a = (Math.PI * 2 * i) / n;
      const r = seeded(ctx, `${key}:r:${i}`, 0.74, 1.28);
      pts.push({ lat: lat + Math.sin(a) * spread * r, lng: lon + Math.cos(a) * spread * r });
    }
    return pts;
  }

  async function renderRainShafts(ctx) {
    ctx.clearRainMarkers();
    const { state } = ctx;
    if (!state.feature.rain || !state.mapReady || !state.maps3dLib || !state.markerLib) return;

    const lod = ctx.cloudLodProfile();
    const now = Date.now();
    const sourceColumns = (state.weatherPayload?.precip_columns || []).filter((c) => Number(c?.estimated_precip_rate_mm_hr || 0) > 1.2);
    const fallbackTiles = state.cloudTiles || [];
    const columns = sourceColumns.length
      ? sourceColumns.slice(0, Math.min(220, lod.tileLimit * 2))
      : fallbackTiles
          .filter((t) => Number(t.precip_rate ?? ((t.precipitation_factor || 0) * 45)) > 1.8)
          .slice(0, Math.min(220, lod.tileLimit * 2))
          .map((t) => ({
            tile_id: t.tile_id,
            lat: Number(t.bounds?.lat_center || 0),
            lon: Number(t.bounds?.lon_center || 0),
            estimated_source_altitude_m: Number(t.base_altitude_m || t.bands?.low?.base_altitude_m || 1600),
            estimated_surface_altitude_m: Number((t.precip_rate || 0) < 4 ? 220 : 0),
            estimated_precip_rate_mm_hr: Number(t.precip_rate ?? ((t.precipitation_factor || 0) * 45)),
            wind_u: Number(t.wind_u ?? t.wind?.mid?.u ?? 0),
            wind_v: Number(t.wind_v ?? t.wind?.mid?.v ?? 0),
          }));

    let markerCount = 0;
    let columnCount = 0;
    const maxColumns = Math.max(18, Math.min(130, Math.round(lod.tileLimit * 1.15)));

    for (const col of columns) {
      if (markerCount >= lod.rainCap || columnCount >= maxColumns) break;
      const lat = Number(col.lat);
      const lon = Number(col.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;

      const precipRate = Number(col.estimated_precip_rate_mm_hr || 0);
      const precipNorm = Math.max(0, Math.min(1, precipRate / 45));
      const topAlt = Math.max(300, Number(col.estimated_source_altitude_m || 1600));
      const bottomAlt = Math.max(0, Number(col.estimated_surface_altitude_m || 0));
      const spread = seeded(ctx, `${col.tile_id}:spread`, 0.022, 0.11) * (0.62 + precipNorm * 1.1);

      // Estimated precip shaft volume using RELATIVE_TO_GROUND column polygon.
      const shaftPath = ctx.buildAltitudePath(columnFootprint(ctx, lat, lon, spread, `${col.tile_id}:shaft`), Math.max(40, topAlt - bottomAlt));
      const shaft = ctx.appendPolygon3D(shaftPath, {
        type: 'column',
        featureType: 'precip-shaft',
        interactive: false,
        extruded: true,
        fillColor: precipColor(precipNorm, 0.16 + precipNorm * 0.36),
        strokeColor: precipColor(precipNorm, 0.22 + precipNorm * 0.32),
        strokeWidth: 0.42,
        zIndex: 66,
        drawsOccludedSegments: true,
      });
      if (shaft) {
        state.rainColumns.push(shaft);
        columnCount += 1;
      }

      const u = Number(col.wind_u || 0);
      const v = Number(col.wind_v || 0);
      const driftLat = (v / 1400) * ((now % 16000) / 16000);
      const driftLon = (u / 1400) * ((now % 16000) / 16000);
      const particles = Math.max(3, Math.min(16, Math.round(4 + precipNorm * 10)));

      for (let i = 0; i < particles; i += 1) {
        if (markerCount >= lod.rainCap) break;
        const phase = ((now / 1000) * (0.34 + precipNorm * 0.68) + i / particles) % 1;
        const altitude = topAlt - (topAlt - bottomAlt) * phase;
        const pLat = lat + seeded(ctx, `${col.tile_id}:p:${i}:lat`, -spread * 1.25, spread * 1.25) + driftLat;
        const pLon = lon + seeded(ctx, `${col.tile_id}:p:${i}:lon`, -spread * 1.25, spread * 1.25) + driftLon;
        const pin = ctx.buildRainSpherePin(state.markerLib, {
          precip: precipNorm,
          scale: 0.16 + precipNorm * 0.32,
          opacity: 0.22 + precipNorm * 0.58,
          colorOverride: precipColor(precipNorm, 0.75),
        });
        const marker = new state.maps3dLib.Marker3DInteractiveElement({
          position: { lat: ctx.clampLat(pLat), lng: ctx.wrapLon(pLon), altitude: Math.max(bottomAlt, altitude) },
        });
        marker.append(pin);
        ctx.globeEl.append(marker);
        state.rainMarkers.push(marker);
        markerCount += 1;
      }
    }
  }

  NS.renderRainShafts = renderRainShafts;
})();
