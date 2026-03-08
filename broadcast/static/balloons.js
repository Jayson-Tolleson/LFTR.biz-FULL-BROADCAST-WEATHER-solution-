export async function renderBalloons(globe) {
  const resp = await fetch('/gfs/api/balloons');
  const payload = await resp.json();

  for (const item of (payload.items || []).slice(0, 90)) {
    const marker = new google.maps.maps3d.Marker3DElement({
      position: { lat: item.lat, lng: item.lng, altitude: 3048 },
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      label: `Jet balloon ${item.speed} kt`
    });

    globe.append(marker);
    window.BROADCAST.layers.balloons.push(marker);
  }
}
