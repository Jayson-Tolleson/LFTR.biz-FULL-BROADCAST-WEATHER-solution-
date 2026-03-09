(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function speedColor(mps) {
    if (mps < 12) return 'rgba(255,255,255,0.24)';
    if (mps < 20) return 'rgba(120,255,255,0.26)';
    if (mps < 28) return 'rgba(130,255,170,0.28)';
    if (mps < 38) return 'rgba(255,240,120,0.30)';
    if (mps < 48) return 'rgba(255,180,110,0.34)';
    return 'rgba(255,95,95,0.40)';
  }

  function renderJetstreamRibbons(ctx) {
    const { state } = ctx;
    if (!state.feature.jet || !state.mapReady || !state.maps3dLib) return 0;

    const lod = ctx.cloudLodProfile();
    const tiles = [...state.cloudTiles]
      .filter((t) => Number(t.bands?.high?.wind?.speed_ms || Math.hypot(Number(t.wind_u || 0), Number(t.wind_v || 0))) > 14)
      .sort((a, b) => Number(b.bands?.high?.wind?.speed_ms || 0) - Number(a.bands?.high?.wind?.speed_ms || 0))
      .slice(0, Math.min(70, lod.tileLimit));

    let added = 0;
    for (const tile of tiles) {
      if (added > Math.max(22, Math.floor(lod.jetCap * 0.32))) break;
      const lat = Number(tile.bounds?.lat_center || 0);
      const lon = Number(tile.bounds?.lon_center || 0);
      const u = Number(tile.bands?.high?.wind?.u ?? tile.wind_u ?? 0);
      const v = Number(tile.bands?.high?.wind?.v ?? tile.wind_v ?? 0);
      const speed = Math.hypot(u, v);
      const heading = Math.atan2(v, u);
      const length = 0.6 + Math.min(1.4, speed / 38);
      const width = 0.06 + Math.min(0.18, speed / 220);
      const p1 = { lat: lat - Math.sin(heading) * width, lng: lon - Math.cos(heading) * width };
      const p2 = { lat: lat + Math.sin(heading) * width, lng: lon + Math.cos(heading) * width };
      const p3 = { lat: lat + Math.sin(heading) * width + Math.cos(heading) * length, lng: lon + Math.cos(heading) * width + Math.sin(heading) * length };
      const p4 = { lat: lat - Math.sin(heading) * width + Math.cos(heading) * length, lng: lon - Math.cos(heading) * width + Math.sin(heading) * length };
      const path = ctx.buildAltitudePath([p1, p2, p3, p4], Number(tile.bands?.high?.base_altitude_m || 9500));
      ctx.appendSimplePolygon(path, speedColor(speed), 'rgba(240,248,255,0.22)', 75, state.jetMarkers, false, '', 'elevated', false);
      added += 1;
    }
    return added;
  }

  async function renderJetLayer(ctx) {
    const added = renderJetstreamRibbons(ctx);
    if (added < 6 && typeof ctx.renderJetBalloonsLegacy === 'function') {
      await ctx.renderJetBalloonsLegacy();
      return;
    }
  }

  NS.renderJetLayer = renderJetLayer;
})();
