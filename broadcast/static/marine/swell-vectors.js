export async function renderSwellVectors(globe) {
  const items = (await (await fetch('/marine/api/swell')).json()).items.slice(0, 600);
  for (const v of items) {
    const marker = new google.maps.maps3d.Marker3DElement({
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      position: { lat: v.lat, lng: v.lng, altitude: 5 },
      label: `Hs ${v.wave_height}m` 
    });
    globe.append(marker);
    window.BROADCAST.layers.swell.push(marker);
  }
}
