const SCALE = [
  { max: 0.2, color: '#ffffff' },
  { max: 1.0, color: '#6ea8ff' },
  { max: 2.5, color: '#27ae60' },
  { max: 5.0, color: '#f1c40f' },
  { max: 8.0, color: '#e67e22' },
  { max: 12.0, color: '#e74c3c' },
  { max: Infinity, color: '#111111' }
];

function rainColor(rate) {
  return SCALE.find((s) => rate <= s.max)?.color || '#111111';
}

export async function renderRainLayer(globe) {
  const resp = await fetch('/gfs/api/rain');
  const payload = await resp.json();

  for (const item of (payload.items || []).slice(0, 180)) {
    const marker = new google.maps.marker.PinElement({
      glyph: '●',
      glyphColor: rainColor(item.rate),
      background: '#00000000',
      borderColor: '#00000000',
      scale: 0.6
    });

    const m = new google.maps.maps3d.Marker3DElement({
      position: { lat: item.lat, lng: item.lng, altitude: 2500 + item.rate * 350 },
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      label: `Rain ${item.rate} mm/h`
    });

    m.append(marker.element);
    globe.append(m);
    window.BROADCAST.layers.rain.push(m);
  }
}
