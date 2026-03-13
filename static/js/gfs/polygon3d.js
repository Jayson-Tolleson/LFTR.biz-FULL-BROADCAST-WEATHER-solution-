function maps3dApi() {
  return window.google?.maps?.maps3d || null;
}

function toLngLatAlt(path, altitude) {
  return (path || []).map((p) => ({ lat: Number(p.lat), lng: Number(p.lng), altitude: Number.isFinite(Number(p.altitude)) ? Number(p.altitude) : altitude }));
}

function applyPolygonStyleSafe(polygon, style) {
  try {
    polygon.fillColor = style.fillColor;
    polygon.strokeColor = style.strokeColor;
    polygon.strokeWidth = style.strokeWidth;
    polygon.extruded = style.extruded;
    polygon.extrudedHeight = style.extrudedHeight;
  } catch (err) {
    throw new Error(`polygon_style_assignment_failed:${err?.message || err}`);
  }
}

function createFallbackPolygon({ coords, altitudeMode, fillColor, fillOpacity, strokeColor, strokeOpacity, strokeWidth, extrudedHeight }) {
  const fallback = document.createElement('gmp-polygon-3d');
  fallback.path = coords;
  fallback.setAttribute('altitude-mode', altitudeMode === 'absolute' ? 'absolute' : 'relative-to-ground');
  fallback.setAttribute('fill-color', fillColor);
  fallback.setAttribute('fill-opacity', String(fillOpacity));
  fallback.setAttribute('stroke-color', strokeColor);
  fallback.setAttribute('stroke-opacity', String(strokeOpacity));
  fallback.setAttribute('stroke-width', String(strokeWidth));
  fallback.setAttribute('extruded', String(extrudedHeight > 0));
  fallback.setAttribute('extruded-height', String(extrudedHeight));
  return fallback;
}

export function createPolygon3D({ path, altitude = 0, altitudeMode = 'relative', fillColor = '#ffffff', fillOpacity = 0.3, strokeColor = '#ffffff', strokeOpacity = 0.7, strokeWidth = 1, extrudedHeight = 0 }) {
  const coords = toLngLatAlt(path, altitude);
  if (coords.length < 3) return null;

  const maps3d = maps3dApi();
  if (maps3d?.Polygon3DElement && maps3d?.AltitudeMode) {
    const mode = altitudeMode === 'absolute' ? maps3d.AltitudeMode.ABSOLUTE : maps3d.AltitudeMode.RELATIVE_TO_GROUND;
    try {
      // keep constructor minimal; assign style fields defensively after init
      const polygon = new maps3d.Polygon3DElement({ altitudeMode: mode });
      polygon.path = coords;
      applyPolygonStyleSafe(polygon, {
        fillColor,
        strokeColor,
        strokeWidth,
        extruded: extrudedHeight > 0,
        extrudedHeight,
      });
      return polygon;
    } catch (err) {
      console.info('[gfs polygon3d] class style unsupported, using fallback element', { message: err?.message || String(err) });
    }
  }

  return createFallbackPolygon({
    coords,
    altitudeMode,
    fillColor,
    fillOpacity,
    strokeColor,
    strokeOpacity,
    strokeWidth,
    extrudedHeight,
  });
}
