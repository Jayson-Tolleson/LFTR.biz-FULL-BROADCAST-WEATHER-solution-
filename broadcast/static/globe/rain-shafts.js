export function startRainShafts(globe, clusters) {
  for (const cluster of clusters.slice(0, 200)) {
    if (cluster.tile.precip_rate <= 0.5) continue;
    const count = Math.min(80, Math.floor(cluster.tile.precip_rate * 6));
    for (let i = 0; i < count; i++) {
      const m = new google.maps.maps3d.Marker3DElement({
        altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
        position: { lat: cluster.tile.lat, lng: cluster.tile.lng, altitude: cluster.tile.base_altitude_m - i * 30 }
      });
      globe.append(m);
      window.BROADCAST.layers.rain.push(m);
    }
  }
}
