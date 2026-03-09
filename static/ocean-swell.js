(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function swellColor(h) {
    if (h < 0.8) return '#93c5fd';
    if (h < 1.5) return '#60a5fa';
    if (h < 2.5) return '#22c55e';
    if (h < 3.5) return '#fde047';
    return '#fb923c';
  }

  function renderOceanSwell(ctx) {
    const state = ctx.state;
    if (!state?.flags?.enableOceanSwell) return;
    if (!state.mapReady || !state.maps3dLib || !state.markerLib) return;
    if (!Array.isArray(state.fish) || !state.fish.length) return;

    if (!Array.isArray(state.swellMarkers)) state.swellMarkers = [];
    state.swellMarkers.forEach((m) => { try { m.remove(); } catch (_) {} });
    state.swellMarkers = [];

    for (const fish of state.fish.slice(0, 80)) {
      const lat = Number(fish.lat);
      const lon = Number(fish.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const h = 0.6 + ctx.seededRange(`${fish.location_key}:swell:h`, 0.1, 3.8);
      const heading = ctx.seededRange(`${fish.location_key}:swell:head`, 0, 360);
      const pin = new state.markerLib.PinElement({
        scale: 0.44,
        glyphText: '↗',
        background: swellColor(h),
        borderColor: 'rgba(255,255,255,0.5)',
        glyphColor: '#0b1020',
      });
      const marker = new state.maps3dLib.Marker3DInteractiveElement({
        position: { lat, lng: lon, altitude: 4 },
        title: `Swell ${h.toFixed(1)}m @ ${heading.toFixed(0)}°`,
      });
      marker.append(pin);
      ctx.globeEl.append(marker);
      state.swellMarkers.push(marker);
    }
  }

  NS.renderOceanSwell = renderOceanSwell;
})();
