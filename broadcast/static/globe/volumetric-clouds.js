export function renderVolumetricClouds(globe, tiles) {
  const clusters = [];
  for (const tile of tiles.slice(0, 300)) {
    const elems = [];
    for (let layer = 0; layer < 8; layer++) {
      const alt = tile.base_altitude_m + ((tile.top_altitude_m - tile.base_altitude_m) * layer / 8);
      const r = 0.06 + tile.coverage * 0.08 + layer * 0.003;
      const pts = [];
      for (let i = 0; i < 12; i++) {
        const a = (i / 12) * Math.PI * 2;
        pts.push({ lat: tile.lat + Math.cos(a) * r, lng: tile.lng + Math.sin(a) * r, altitude: alt });
      }
      const poly = new google.maps.maps3d.Polygon3DElement({
        altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
        fillColor: `rgba(255,255,255,${0.35 + layer * 0.03})`,
        strokeWidth: 0,
        outerCoordinates: pts
      });
      globe.append(poly);
      elems.push(poly);
      window.BROADCAST.layers.clouds.push(poly);
    }
    clusters.push({ tile, elements: elems });
  }
  return clusters;
}

export function startCloudAdvection(globe, clusters) {
  let prev = performance.now();
  function tick(now) {
    const dt = Math.max(0.016, (now - prev) / 1000);
    prev = now;
    for (const cluster of clusters) {
      const dLat = cluster.tile.wind_v * dt * 0.00001;
      const dLng = cluster.tile.wind_u * dt * 0.00001;
      cluster.tile.lat += dLat;
      cluster.tile.lng += dLng;
      for (const e of cluster.elements) {
        e.outerCoordinates = (e.outerCoordinates || []).map((p) => ({ ...p, lat: p.lat + dLat, lng: p.lng + dLng }));
      }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
