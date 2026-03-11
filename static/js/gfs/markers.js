export function renderMarkers({ locations, globeEl, maps3d, onSelect }) {
  const active = [];
  const template = document.getElementById('fish-orb-template');

  if (!template) {
    console.error('[gfs/markers] fish-orb-template missing');
    return () => {};
  }

  for (const loc of locations) {
    const markerContent = template.content.cloneNode(true);

    const m = new maps3d.Marker3DInteractiveElement({
      position: {
        lat: loc.lat,
        lng: loc.lon,
        altitude: 18,
      },
      title: loc.name,
      drawsWhenOccluded: true,
      extruded: false,
    });

    m.append(markerContent);

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
