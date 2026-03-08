export function startLightning(globe, clusters) {
  function flash() {
    const storms = clusters.filter((c) => c.tile.storm_energy > 0.5 && c.tile.precip_rate > 5);
    for (const s of storms.slice(0, 15)) {
      const pts = [];
      let lat = s.tile.lat;
      let lng = s.tile.lng;
      let alt = (s.tile.base_altitude_m + s.tile.top_altitude_m) / 2;
      for (let i = 0; i < 12; i++) {
        lat += (Math.random() - 0.5) * 0.02;
        lng += (Math.random() - 0.5) * 0.02;
        alt = Math.max(0, alt - (200 + Math.random() * 350));
        pts.push({ lat, lng, altitude: alt });
      }
      const bolt = new google.maps.maps3d.Polyline3DElement({
        altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
        strokeColor: 'rgba(255,255,255,0.95)',
        strokeWidth: 3,
        coordinates: pts
      });
      globe.append(bolt);
      window.BROADCAST.layers.lightning.push(bolt);
      setTimeout(() => bolt.remove(), 450);
    }
    setTimeout(flash, 1800 + Math.random() * 5000);
  }
  flash();
}
