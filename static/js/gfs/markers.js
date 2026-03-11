export function buildFishOrb() {
  const el = document.createElement('div');
  el.className = 'fish-orb';
  return el;
}

export function renderMarkers({ locations, globeEl, maps3d, marker, onSelect }) {
  const active = [];
  for (const loc of locations) {
    const pin = new marker.PinElement({ scale: 1.2, background: '#14ff70', borderColor: '#dcfce7', glyphColor: '#ecfff1' });
    const m = new maps3d.Marker3DInteractiveElement({
      position: { lat: loc.lat, lng: loc.lon, altitude: 15 },
      title: loc.name,
    });
    m.append(pin);
    m.addEventListener('gmp-click', () => onSelect(loc));
    m.addEventListener('click', () => onSelect(loc));
    globeEl.append(m);
    active.push(m);
  }
  return () => active.forEach((m) => { try { m.remove(); } catch (_) {} });
}
