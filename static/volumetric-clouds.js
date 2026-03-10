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

  function regimeProfile(regime) {
    if (regime === 'deep_convection') return { threshold: 0.34, layers: 7, lobes: 12, spread: 1.12, anvil: 1.45 };
    if (regime === 'marine_stratocumulus' || regime === 'frontal_shield') return { threshold: 0.28, layers: 4, lobes: 8, spread: 1.32, anvil: 1.0 };
    if (regime === 'cirrus_sheet') return { threshold: 0.24, layers: 5, lobes: 7, spread: 1.52, anvil: 1.08 };
    return { threshold: 0.31, layers: 5, lobes: 9, spread: 1.18, anvil: 1.12 };
  }

  function ring(cx, cy, radiusLat, radiusLon, n, key) {
    const pts = [];
    for (let i = 0; i < n; i += 1) {
      const a = (Math.PI * 2 * i) / n;
      const r = 0.72 + hashUnit(`${key}:${i}`) * 0.48;
      pts.push({ lat: cx + Math.sin(a) * radiusLat * r, lng: cy + Math.cos(a) * radiusLon * r });
    }
    return pts;
  }

  function cloudMassParts(ctx, tile, lod) {
    const regime = tile.regime || 'cumulus_field';
    const p = regimeProfile(regime);
    const density = Number(tile.estimated_density ?? tile.density ?? ((tile.low_density || 0) * 0.4 + (tile.mid_density || 0) * 0.35 + (tile.high_density || 0) * 0.25));
    const stormEnergy = Number(tile.storm_energy ?? tile.convection_factor ?? 0);
    const baseLat = Number(tile.bounds?.lat_center || 0);
    const baseLon = Number(tile.bounds?.lon_center || 0);
    if (!Number.isFinite(baseLat) || !Number.isFinite(baseLon)) return [];

    const lowBand = tile.bands?.low || {};
    const midBand = tile.bands?.mid || {};
    const scaleKm = Number(midBand.lateral_scale_km || lowBand.lateral_scale_km || 85);
    const latExtent = (0.09 + scaleKm / 240) * p.spread;
    const lonExtent = (0.1 + scaleKm / 220) * p.spread;
    const baseAlt = Number(tile.estimated_cloud_base_m || tile.base_altitude_m || lowBand.base_altitude_m || 1000);
    const topAlt = Number(tile.estimated_cloud_top_m || tile.top_altitude_m || tile.bands?.high?.top_altitude_m || 9200);
    const depth = Math.max(700, topAlt - baseAlt);

    const layers = Math.max(3, Math.min(lod.label === 'low-alt' ? 8 : 6, p.layers + (lod.label === 'low-alt' ? 1 : 0)));
    const lobeCount = Math.max(5, Math.min(lod.label === 'low-alt' ? 18 : 12, p.lobes + Math.round(stormEnergy * 4)));
    const parts = [];

    // Base deck improves high-alt readability for large systems.
    const deckRing = ring(baseLat, baseLon, latExtent * 1.15, lonExtent * 1.15, 14, `${tile.tile_id}:deck`);
    parts.push({ role: 'deck', path: ctx.buildAltitudePath(deckRing, baseAlt + 40), z: 8, occ: Math.max(0.18, density * 0.7), stormEnergy, layerT: 0.08, interactive: false, extruded: true });

    for (let li = 0; li < layers; li += 1) {
      const lt = li / Math.max(1, layers - 1);
      const layerAlt = baseAlt + depth * lt;
      const layerSpread = 1 + (regime === 'deep_convection' && lt > 0.72 ? p.anvil * (lt - 0.65) : 0);
      for (let i = 0; i < lobeCount; i += 1) {
        const key = `${tile.tile_id || 'tile'}:${tile.seed || 0}:${li}:${i}`;
        const occ = density * (0.54 + hashUnit(`${key}:occ`) * 0.68) * (1 - Math.abs(lt - 0.52) * 0.62);
        if (occ < p.threshold) continue;

        const angle = hashUnit(`${key}:a`) * Math.PI * 2;
        const radial = Math.sqrt(hashUnit(`${key}:r`));
        const ox = Math.cos(angle) * radial;
        const oy = Math.sin(angle) * radial;
        const cx = baseLat + ox * latExtent * (0.56 + 0.42 * hashUnit(`${key}:sx`));
        const cy = baseLon + oy * lonExtent * layerSpread * (0.56 + 0.42 * hashUnit(`${key}:sy`));
        const rLat = (latExtent / (2.6 + lobeCount * 0.12)) * (0.75 + hashUnit(`${key}:rl`) * 1.2);
        const rLon = (lonExtent / (2.7 + lobeCount * 0.11)) * (0.75 + hashUnit(`${key}:rw`) * 1.22) * layerSpread;
        const altJitter = (hashUnit(`${key}:alt`) - 0.5) * 210;
        parts.push({
          role: lt > 0.72 ? 'anvil' : (lt > 0.46 ? 'core' : 'tower'),
          path: ctx.buildAltitudePath(ring(cx, cy, rLat, rLon, 10, key), layerAlt + altJitter),
          z: 10 + li,
          occ,
          stormEnergy,
          layerT: lt,
          interactive: regime === 'deep_convection' && occ > 0.72 && Number(tile.importance || 0) > 0.62,
          extruded: lt < 0.85,
          title: `Cloud ${tile.tile_id || ''} • est ${Math.round(occ * 100)}%`,
        });
      }
    }

    return parts;
  }

  function cloudPaint(ctx, part) {
    const sun = ctx.getSunState ? ctx.getSunState() : { daylight: 0.7 };
    const shade = Math.max(160, Math.min(245, Math.round(229 - part.stormEnergy * 34 - part.layerT * 19 + sun.daylight * 10)));
    const alpha = Math.max(0.08, Math.min(0.66, 0.12 + part.occ * 0.46 - part.layerT * 0.09 + part.stormEnergy * 0.08));
    return {
      fillColor: `rgba(${shade},${Math.min(252, shade + 7)},${Math.min(255, shade + 16)},${alpha.toFixed(3)})`,
      strokeColor: `rgba(245,249,255,${Math.max(0.05, alpha * 0.36).toFixed(3)})`,
      strokeWidth: part.layerT > 0.7 ? 0.32 : 0.54,
      zIndex: part.z,
    };
  }

  async function renderCloudVolumes(ctx) {
    ctx.clearCloudPolygons();
    const { state } = ctx;
    if (!state.feature.clouds || !state.mapReady || !state.cloudTiles.length || !state.maps3dLib) return;

    const lod = ctx.cloudLodProfile();
    const tiles = [...state.cloudTiles].sort((a, b) => Number(b.importance || 0) - Number(a.importance || 0)).slice(0, lod.tileLimit);
    let budget = 0;

    for (const tile of tiles) {
      if (budget >= lod.cloudPartBudget) break;
      const parts = cloudMassParts(ctx, tile, lod);
      for (const part of parts) {
        if (budget >= lod.cloudPartBudget) break;
        try {
          const paint = cloudPaint(ctx, part);
          const poly = ctx.appendPolygon3D(part.path, {
            type: 'elevated',
            featureType: `cloud-${part.role}`,
            interactive: !!part.interactive,
            extruded: !!part.extruded,
            fillColor: paint.fillColor,
            strokeColor: paint.strokeColor,
            strokeWidth: paint.strokeWidth,
            zIndex: paint.zIndex,
            drawsOccludedSegments: true,
          });
          if (!poly) continue;
          if (part.interactive) {
            poly.addEventListener('gmp-click', () => ctx.setHudMessage(part.title || 'Cloud cell'));
            state.cloudInteractivePolygons.push(poly);
          } else {
            state.cloudPolygons.push(poly);
          }
          budget += 1;
        } catch (err) {
          console.warn('[gfs] cloud part render skipped', err);
        }
      }
    }
  }

  NS.renderCloudVolumes = renderCloudVolumes;
})();
