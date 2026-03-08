export async function renderFish(globe) {
  const [apiResp, csvResp] = await Promise.all([
    fetch('/gfs/api/fish'),
    fetch('/data/fishloclist.csv')
  ]);
  await csvResp.text();
  const payload = await apiResp.json();

  for (const fish of payload.items || []) {
    const marker = new google.maps.maps3d.Marker3DElement({
      position: { lat: fish.lat, lng: fish.lng, altitude: 40 },
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      label: `🐟 ${fish.name}`
    });

    marker.addEventListener('click', () => {
      if (typeof window.BROADCAST.onFishSelected === 'function') {
        window.BROADCAST.onFishSelected(fish);
      }
    });

    globe.append(marker);
    window.BROADCAST.layers.fish.push(marker);
  }
}
