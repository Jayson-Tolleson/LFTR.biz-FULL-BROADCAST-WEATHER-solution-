function wrapLng(lng) {
  if (lng > 180) return lng - 360;
  if (lng < -180) return lng + 360;
  return lng;
}

function wrapLat(lat) {
  if (lat > 80) return -80 + (lat - 80);
  if (lat < -80) return 80 + (lat + 80);
  return lat;
}

export function startCloudAdvection(globe, cloudClusters, shiftCluster) {
  let prevTs = performance.now();
  let frame = 0;

  function updateCloudAdvection(now) {
    const dt = Math.max(0.016, (now - prevTs) / 1000);
    prevTs = now;
    frame += 1;

    const altitude = globe?.center?.altitude ?? 18000000;
    const skip = altitude < 900000 ? (frame % 2 === 0) : false;
    if (!skip) {
      for (const cluster of cloudClusters) {
        const oldU = cluster.wind_u ?? cluster.tile.wind_u;
        const oldV = cluster.wind_v ?? cluster.tile.wind_v;
        const alpha = 0.06;
        cluster.wind_u = oldU + ((cluster.tile.wind_u - oldU) * alpha);
        cluster.wind_v = oldV + ((cluster.tile.wind_v - oldV) * alpha);

        const dLat = cluster.wind_v * dt * 0.00001;
        const dLng = cluster.wind_u * dt * 0.00001;
        shiftCluster(cluster, dLat, dLng, 1.0 + (Math.random() - 0.5) * 0.1);

        cluster.tile.lat = wrapLat(cluster.tile.lat);
        cluster.tile.lng = wrapLng(cluster.tile.lng);
      }
    }

    requestAnimationFrame(updateCloudAdvection);
  }

  requestAnimationFrame(updateCloudAdvection);
}
