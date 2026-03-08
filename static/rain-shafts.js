(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function seeded(ctx, key, min, max) {
    return ctx.seededRange ? ctx.seededRange(key, min, max) : (min + (max - min) * 0.5);
  }

  async function renderRainShafts(ctx) {
    ctx.clearRainMarkers();
    const { state } = ctx;
    if (!state.feature.rain || !state.mapReady || !state.cloudTiles.length || !state.maps3dLib || !state.markerLib) return;

    const lod = ctx.cloudLodProfile();
    const now = Date.now();
    const stormTiles = state.cloudTiles
      .filter((t) => Number(t.precip_rate ?? ((t.precipitation_factor || 0) * 45)) > 2.2)
      .sort((a, b) => Number(b.precip_rate ?? (b.precipitation_factor || 0)) - Number(a.precip_rate ?? (a.precipitation_factor || 0)))
      .slice(0, Math.min(lod.tileLimit, 230));

    let count = 0;
    for (const tile of stormTiles) {
      const precipRate = Number(tile.precip_rate ?? ((tile.precipitation_factor || 0) * 45));
      const precip = Math.max(0, Math.min(1, precipRate / 45));
      const baseLat = Number(tile.bounds?.lat_center || 0);
      const baseLon = Number(tile.bounds?.lon_center || 0);
      const topAlt = Number(tile.base_altitude_m || tile.bands?.low?.base_altitude_m || tile.bands?.mid?.base_altitude_m || 2200);
      const virga = precip < 0.18 ? seeded(ctx, `${tile.tile_id}:virga`, 120, 780) : 0;
      const bottomAlt = Math.max(0, virga);
      const u = Number(tile.wind_u ?? tile.wind?.mid?.u ?? 0);
      const v = Number(tile.wind_v ?? tile.wind?.mid?.v ?? 0);
      const driftLat = (v / 1200) * ((now % 14000) / 14000);
      const driftLon = (u / 1200) * ((now % 14000) / 14000);

      const shafts = Math.max(1, Math.min(10, Math.round(1 + precip * 9)));
      for (let s = 0; s < shafts; s += 1) {
        const key = `${tile.tile_id || 'tile'}:shaft:${s}:${tile.seed || 0}`;
        const lat = baseLat + seeded(ctx, `${key}:lat`, -0.22, 0.22) + driftLat;
        const lon = baseLon + seeded(ctx, `${key}:lon`, -0.22, 0.22) + driftLon;
        const particles = Math.max(4, Math.min(20, Math.round(6 + precip * 14)));

        for (let i = 0; i < particles; i += 1) {
          if (count >= lod.rainCap) break;
          const phase = ((now / 1000) * (0.36 + precip * 0.64) + i / particles) % 1;
          const altitude = topAlt - (topAlt - bottomAlt) * phase;
          const rainParticle = {
            precip,
            scale: 0.2 + precip * 0.3,
            opacity: Math.max(0.18, Math.min(0.82, 0.25 + precip * 0.55 - phase * 0.2)),
          };
          const pin = ctx.buildRainSpherePin(state.markerLib, rainParticle);
          const marker = new state.maps3dLib.Marker3DInteractiveElement({
            position: { lat: ctx.clampLat(lat), lng: ctx.wrapLon(lon), altitude: Math.max(bottomAlt, altitude) },
          });
          marker.append(pin);
          ctx.globeEl.append(marker);
          state.rainMarkers.push(marker);
          count += 1;
        }
      }
      if (count >= lod.rainCap) break;
    }

    if (typeof NS.renderHailAndLightning === 'function') {
      NS.renderHailAndLightning(ctx, lod);
    } else if (typeof ctx.renderHailAndLightningLegacy === 'function') {
      ctx.renderHailAndLightningLegacy(lod);
    }
  }

  NS.renderRainShafts = renderRainShafts;
})();
