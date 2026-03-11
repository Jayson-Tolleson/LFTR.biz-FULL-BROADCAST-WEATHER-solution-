function hudTextForLocation(loc) {
  if (typeof loc.probability === 'number') {
    return `P=${Math.round(loc.probability * 100)}%`;
  }
  const lat = Number(loc.lat || 0).toFixed(2);
  const lon = Number(loc.lon || 0).toFixed(2);
  return `${lat}, ${lon}`;
}

function buildMarkerTemplate(baseTemplate, loc) {
  const tpl = baseTemplate.cloneNode(true);
  const label = tpl.content.querySelector('.fish-orb-hud-label');
  if (label) {
    label.textContent = hudTextForLocation(loc);
  }
  return tpl;
}

export function renderMarkers({ locations, globeEl, maps3d, onSelect }) {
  const active = [];
  const baseTemplate = document.getElementById('fish-orb-template');

  if (!(baseTemplate instanceof HTMLTemplateElement)) {
    console.error('[gfs/markers] fish-orb-template missing');
    return () => {};
  }

  const altitudeMode = maps3d?.AltitudeMode?.RELATIVE_TO_GROUND || 'RELATIVE_TO_GROUND';

  for (const loc of locations) {
    const markerTemplate = buildMarkerTemplate(baseTemplate, loc);

    const m = new maps3d.Marker3DInteractiveElement({
      position: {
        lat: loc.lat,
        lng: loc.lon,
        altitude: 18,
      },
      altitudeMode,
      title: loc.name,
      drawsWhenOccluded: false,
      extruded: false,
    });

    m.append(markerTemplate);

    const click = () => onSelect(loc);
    m.addEventListener('gmp-click', click);

    globeEl.append(m);
    active.push(m);
  }

  return () => {
    active.forEach((m) => {
      try { m.remove(); } catch (_) {}
    });
  };
}
