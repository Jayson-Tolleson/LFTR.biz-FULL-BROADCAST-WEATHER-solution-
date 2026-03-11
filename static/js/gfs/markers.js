function createOrbNode() {
  const root = document.createElement('div');
  root.className = 'fish-orb3d';
  root.innerHTML = '<span class="fish-orb3d__halo"></span><span class="fish-orb3d__glass"></span><span class="fish-orb3d__core"></span><span class="fish-orb3d__spec"></span>';
  return root;
}

export function renderMarkers({ locations, globeEl, maps3d, onSelect }) {
  const active = [];
  for (const loc of locations) {
    const orb = createOrbNode();
    const m = new maps3d.Marker3DInteractiveElement({
      position: { lat: loc.lat, lng: loc.lon, altitude: 18 },
      title: loc.name,
      drawsWhenOccluded: true,
      extruded: false,
    });
    m.append(orb);
    const click = () => onSelect(loc);
    m.addEventListener('gmp-click', click);
    m.addEventListener('click', click);
    m.addEventListener('mouseenter', () => orb.classList.add('is-hover'));
    m.addEventListener('mouseleave', () => orb.classList.remove('is-hover'));
    globeEl.append(m);
    active.push(m);
  }
  return () => active.forEach((m) => { try { m.remove(); } catch (_) {} });
}
