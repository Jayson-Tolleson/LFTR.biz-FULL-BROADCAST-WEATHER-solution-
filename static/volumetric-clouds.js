(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function hashUnit(key) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < key.length; i += 1) {
      h ^= key.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0) / 4294967295;
  }

  function presetForRegime(regime) {
    if (regime === 'deep_convection') return { kind: 'CUMULONIMBUS', threshold: 0.43, spread: 1.05, anvil: 1.35 };
    if (regime === 'marine_stratocumulus' || regime === 'frontal_shield') return { kind: 'STRATOCUMULUS', threshold: 0.34, spread: 1.45, anvil: 1.0 };
    return { kind: 'CUMULUS', threshold: 0.37, spread: 1.15, anvil: 1.12 };
  }

  function ringForCell(cx, cy, radiusLat, radiusLon, n, key) {
    const pts = [];
    for (let i = 0; i < n; i += 1) {
      const a = (Math.PI * 2 * i) / n;
      const r = 0.75 + hashUnit(`${key}:${i}`) * 0.45;
      pts.push({ lat: cx + Math.sin(a) * radiusLat * r, lng: cy + Math.cos(a) * radiusLon * r });
    }
    return pts;
  }

  function addCloudPoly(ctx, spec, interactive) {
    const poly = ctx.appendPolygon3D(spec.path, {
      type: 'elevated',
      featureType: `cloud-${spec.role || 'billow'}`,
      interactive,
      extruded: !!spec.extruded,
      fillColor: spec.fillColor,
      strokeColor: spec.strokeColor,
      strokeWidth: spec.strokeWidth || 0.5,
      zIndex: spec.zIndex || 20,
      innerPaths: spec.innerPaths || [],
      drawsOccludedSegments: true,
    });
    if (!poly) return null;
    if (interactive) {
      poly.addEventListener('gmp-click', () => {
        ctx.setHudMessage(`Storm ${spec.tileId || ''} • energy ${Math.round((spec.stormEnergy || 0) * 100)}%`);
      });
      ctx.state.cloudInteractivePolygons.push(poly);
    } else {
      ctx.state.cloudPolygons.push(poly);
    }
    return poly;
  }

  async function renderCloudVolumes(ctx) {
    ctx.clearCloudPolygons();
    const { state } = ctx;
    if (!state.feature.clouds || !state.mapReady || !state.cloudTiles.length || !state.maps3dLib) return;

    const lod = ctx.cloudLodProfile();
    const now = Date.now();
    const tiles = [...state.cloudTiles].sort((a, b) => Number(b.importance || 0) - Number(a.importance || 0)).slice(0, lod.tileLimit);
    const isLow = lod.label === 'low-alt';
    const nx = isLow ? 16 : lod.label === 'mid-alt' ? 14 : 12;
    const ny = isLow ? 16 : lod.label === 'mid-alt' ? 14 : 12;
    const nz = isLow ? 10 : lod.label === 'mid-alt' ? 8 : 6;

    let budget = 0;
    for (const tile of tiles) {
      if (budget >= lod.cloudPartBudget) break;
      const regime = tile.regime || 'cumulus_field';
      const preset = presetForRegime(regime);
      const advection = NS.advectTile ? NS.advectTile(tile, now) : null;

      const baseLat = Number(tile.bounds?.lat_center || 0);
      const baseLon = Number(tile.bounds?.lon_center || 0);
      const lateralKm = Number(tile.bands?.mid?.lateral_scale_km || tile.bands?.low?.lateral_scale_km || 90);
      const latExtent = 0.08 + lateralKm / 260;
      const lonExtent = (0.1 + lateralKm / 220) * preset.spread;

      const zBase = Number(tile.base_altitude_m || tile.bands?.low?.base_altitude_m || 1000);
      const zTop = Number(tile.top_altitude_m || tile.bands?.high?.top_altitude_m || 9000);
      const zSpan = Math.max(600, zTop - zBase);
      const density = Number(tile.density ?? ((tile.low_density || 0) * 0.4 + (tile.mid_density || 0) * 0.35 + (tile.high_density || 0) * 0.25));
      const stormEnergy = Number(tile.storm_energy ?? (tile.convection_factor || 0));

      for (let iz = 0; iz < nz; iz += 1) {
        if (budget >= lod.cloudPartBudget) break;
        const zt = iz / Math.max(1, nz - 1);
        const layerAlt = zBase + zSpan * zt;
        const anvilMul = regime === 'deep_convection' && zt > 0.68 ? preset.anvil : 1;
        for (let iy = 0; iy < ny; iy += 1) {
          if (budget >= lod.cloudPartBudget) break;
          for (let ix = 0; ix < nx; ix += 1) {
            if (budget >= lod.cloudPartBudget) break;
            const key = `${tile.tile_id || 'tile'}:${tile.seed || 0}:${ix}:${iy}:${iz}`;
            const n = hashUnit(key);
            const occ = density * (0.55 + n * 0.65) * (1 - Math.abs(zt - 0.52) * 0.62);
            if (occ < preset.threshold) continue;

            const gx = (ix / Math.max(1, nx - 1) - 0.5) * 2;
            const gy = (iy / Math.max(1, ny - 1) - 0.5) * 2;
            const driftBand = zt > 0.66 ? 'high' : zt > 0.33 ? 'mid' : 'low';
            const drift = advection?.[driftBand] || { lat: baseLat, lon: baseLon };

            const cx = drift.lat + gx * latExtent * (0.78 + hashUnit(`${key}:gx`) * 0.4);
            const cy = drift.lon + gy * lonExtent * (0.78 + hashUnit(`${key}:gy`) * 0.4) * anvilMul;
            const rLat = latExtent / nx * (0.72 + n * 1.12);
            const rLon = lonExtent / ny * (0.72 + n * 1.18) * anvilMul;
            const ring = ringForCell(cx, cy, rLat, rLon, 10, key);
            const path = ctx.buildAltitudePath(ring, layerAlt + hashUnit(`${key}:alt`) * 180 - 90);

            const alpha = Math.max(0.08, Math.min(0.62, 0.14 + occ * 0.44 - zt * 0.08 + stormEnergy * 0.08));
            const shade = Math.max(170, Math.min(244, Math.round(230 - stormEnergy * 34 - zt * 16 + (ctx.getSunState().daylight * 12))));
            const spec = {
              role: regime === 'deep_convection' && zt > 0.66 ? 'anvil' : (zt > 0.62 ? 'wispy' : 'tower'),
              tileId: tile.tile_id,
              stormEnergy,
              extruded: zt < 0.82,
              fillColor: `rgba(${shade},${Math.min(252, shade + 6)},${Math.min(255, shade + 14)},${alpha.toFixed(3)})`,
              strokeColor: `rgba(245,249,255,${Math.max(0.06, alpha * 0.35).toFixed(3)})`,
              strokeWidth: zt > 0.75 ? 0.34 : 0.52,
              path,
              zIndex: 20 + iz,
            };
            const interactive = regime === 'deep_convection' && stormEnergy > 0.55 && iz > Math.floor(nz * 0.45) && budget % 17 === 0;
            if (addCloudPoly(ctx, spec, interactive)) budget += 1;
          }
        }
      }
    }
  }

  NS.renderCloudVolumes = renderCloudVolumes;
})();
