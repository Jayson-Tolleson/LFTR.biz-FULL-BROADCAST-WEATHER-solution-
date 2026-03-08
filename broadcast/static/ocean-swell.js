const MAX_SWELL_VECTORS = 600;
const vectors = [];

function colorByWaveHeight(h) {
  if (h < 1) return '#2c6df2';
  if (h < 2) return '#2cd4f2';
  if (h < 3) return '#46d96f';
  if (h < 4) return '#f1d44f';
  if (h < 6) return '#f28c3a';
  return '#f05252';
}

function densityByAltitude(globe) {
  const altitude = globe?.center?.altitude ?? 18000000;
  if (altitude > 5000000) return 4;
  if (altitude > 1200000) return 2;
  return 1;
}

function toHeading(directionDeg) {
  const rad = directionDeg * Math.PI / 180;
  return { dx: Math.sin(rad), dy: Math.cos(rad) };
}

export async function renderOceanSwell(globe) {
  const resp = await fetch('/gfs/api/swell');
  const payload = await resp.json();
  const all = (payload.items || []).slice(0, MAX_SWELL_VECTORS);

  const gridStep = densityByAltitude(globe);
  const filtered = all.filter((_, idx) => idx % gridStep === 0);

  for (const item of filtered) {
    const heading = toHeading(item.wave_direction);
    const pin = new google.maps.marker.PinElement({
      glyph: '➤',
      glyphColor: '#0b1020',
      background: colorByWaveHeight(item.wave_height),
      borderColor: '#ffffff',
      scale: 0.65
    });

    const marker = new google.maps.maps3d.Marker3DElement({
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      position: { lat: item.lat, lng: item.lng, altitude: 5 },
      label: `Hs ${item.wave_height}m / Tp ${item.wave_period}s`
    });

    marker.append(pin.element);
    globe.append(marker);
    window.BROADCAST.layers.swell.push(marker);
    vectors.push({
      marker,
      lat: item.lat,
      lng: item.lng,
      heading,
      wave_period: item.wave_period,
      wave_height: item.wave_height
    });
  }

  function updateSwell() {
    for (const v of vectors) {
      const baseSpeed = v.wave_period * 0.000001;
      const jitter = v.wave_period < 9 ? (Math.random() - 0.5) * 0.000003 : 0;
      const speed = baseSpeed + jitter;

      v.lat += v.heading.dy * (0.00002 + speed);
      v.lng += v.heading.dx * (0.00002 + speed);

      if (v.lng > 180) v.lng -= 360;
      if (v.lng < -180) v.lng += 360;
      if (v.lat > 82) v.lat = -82;
      if (v.lat < -82) v.lat = 82;

      v.marker.position = { lat: v.lat, lng: v.lng, altitude: 5 };
    }
    requestAnimationFrame(updateSwell);
  }
  requestAnimationFrame(updateSwell);
}
