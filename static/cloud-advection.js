(function () {
  const NS = (window.LFTRWeather = window.LFTRWeather || {});

  function advectTile(tile, nowMs) {
    const updatedAt = Number(tile?.updated_at || nowMs);
    const dtHours = Math.max(0, (nowMs - updatedAt) / 3600000);
    const u = Number(tile?.wind_u ?? tile?.wind?.mid?.u ?? 0);
    const v = Number(tile?.wind_v ?? tile?.wind?.mid?.v ?? 0);
    const lat = Number(tile?.bounds?.lat_center ?? 0);
    const lon = Number(tile?.bounds?.lon_center ?? 0);
    const shear = Math.max(0, Number(tile?.wind_shear ?? 0));
    const latFactor = 0.028;
    const lonFactor = 0.028 / Math.max(0.2, Math.cos((lat * Math.PI) / 180));

    const lowMul = 0.92;
    const midMul = 1.0;
    const highMul = 1.06 + shear * 0.12;

    return {
      low: { lat: lat + v * dtHours * latFactor * lowMul, lon: lon + u * dtHours * lonFactor * lowMul },
      mid: { lat: lat + v * dtHours * latFactor * midMul, lon: lon + u * dtHours * lonFactor * midMul },
      high: { lat: lat + v * dtHours * latFactor * highMul, lon: lon + u * dtHours * lonFactor * highMul },
    };
  }

  NS.advectTile = advectTile;
})();
