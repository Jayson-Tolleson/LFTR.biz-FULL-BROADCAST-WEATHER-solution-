const MAX_ACTIVE_BOLTS = 15;
const MAX_SEGMENTS = 30;

function rand(min, max) {
  return min + Math.random() * (max - min);
}

function tileFlashIntervalMs(tile) {
  const energy = Math.max(0.5, tile.storm_energy || 0.5);
  const norm = (energy - 0.5) / 0.5;
  return (2 + (1 - norm) * 8) * 1000;
}

function isLightningTile(tile) {
  return tile.storm_energy > 0.5 && tile.precip_rate > 5 && tile.top_altitude_m > 9000;
}

function generateBolt(tile, lowDetail = false) {
  const start = {
    lat: tile.lat,
    lng: tile.lng,
    altitude: (tile.base_altitude_m + tile.top_altitude_m) / 2
  };

  const segments = [start];
  let current = { ...start };
  const maxLen = lowDetail ? 10 : 18;
  const branchChance = lowDetail ? 0.08 : 0.2;

  for (let i = 0; i < maxLen && segments.length < MAX_SEGMENTS; i++) {
    current = {
      lat: current.lat + rand(-0.01, 0.01),
      lng: current.lng + rand(-0.01, 0.01),
      altitude: Math.max(0, current.altitude - rand(200, 600))
    };
    segments.push(current);
  }

  const branches = [];
  if (!lowDetail) {
    for (let i = 2; i < segments.length - 2; i++) {
      if (Math.random() > branchChance) continue;
      const branch = [segments[i]];
      let node = { ...segments[i] };
      const len = Math.floor(rand(3, 7));
      for (let b = 0; b < len; b++) {
        node = {
          lat: node.lat + rand(-0.008, 0.008),
          lng: node.lng + rand(-0.008, 0.008),
          altitude: Math.max(0, node.altitude - rand(120, 380))
        };
        branch.push(node);
      }
      branches.push(branch);
    }
  }

  if (Math.random() < 0.1 && segments.length) {
    segments[segments.length - 1] = { ...segments[segments.length - 1], altitude: 0 };
  }

  return { segments, branches, duration: rand(100, 200), fade: 300 };
}

function estimateDistanceMeters(a, b) {
  const latScale = 111000;
  const lngScale = 111000 * Math.cos((a.lat * Math.PI) / 180);
  const dLat = (a.lat - b.lat) * latScale;
  const dLng = (a.lng - b.lng) * lngScale;
  const dAlt = (a.altitude || 0) - (b.altitude || 0);
  return Math.sqrt(dLat * dLat + dLng * dLng + dAlt * dAlt);
}

function createPolyline(coords, width, color) {
  return new google.maps.maps3d.Polyline3DElement({
    altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
    strokeColor: color,
    strokeWidth: width,
    coordinates: coords
  });
}

export function startLightningSystem(globe, cloudClusters, flashCloudCluster) {
  const stormClusters = cloudClusters.filter((cluster) => isLightningTile(cluster.tile));
  const schedulers = stormClusters.map((cluster) => ({
    cluster,
    nextAt: performance.now() + rand(500, tileFlashIntervalMs(cluster.tile))
  }));

  const activeBolts = [];

  function removeBolt(bolt) {
    for (const elem of bolt.elements) {
      try { elem.remove(); } catch (e) { /* noop */ }
    }
    const idx = activeBolts.indexOf(bolt);
    if (idx >= 0) activeBolts.splice(idx, 1);
  }

  function spawnBolt(cluster) {
    if (activeBolts.length >= MAX_ACTIVE_BOLTS) return;

    const lowDetail = (globe?.center?.altitude ?? 18000000) > 2500000;
    const boltDef = generateBolt(cluster.tile, lowDetail);
    const mainGlow = createPolyline(boltDef.segments, 5, 'rgba(120,170,255,0.45)');
    const mainCore = createPolyline(boltDef.segments, 3, 'rgba(255,255,255,0.95)');

    const elements = [mainGlow, mainCore];
    globe.append(mainGlow);
    globe.append(mainCore);

    if (!lowDetail) {
      for (const branch of boltDef.branches) {
        const branchGlow = createPolyline(branch, 3, 'rgba(120,170,255,0.35)');
        const branchCore = createPolyline(branch, 1.8, 'rgba(255,255,255,0.8)');
        globe.append(branchGlow);
        globe.append(branchCore);
        elements.push(branchGlow, branchCore);
      }
    }

    flashCloudCluster(cluster, 0.32, boltDef.duration);

    const camera = globe?.center || { lat: cluster.tile.lat, lng: cluster.tile.lng, altitude: 0 };
    const distance = estimateDistanceMeters(camera, boltDef.segments[0]);
    const thunderDelay = distance / 343;
    window.setTimeout(() => console.debug('Thunder delay(s):', thunderDelay.toFixed(2)), thunderDelay * 1000);

    const bolt = { elements };
    activeBolts.push(bolt);
    window.setTimeout(() => removeBolt(bolt), boltDef.duration + boltDef.fade);
  }

  function tick() {
    const now = performance.now();
    for (const scheduler of schedulers) {
      if (now < scheduler.nextAt) continue;
      spawnBolt(scheduler.cluster);
      scheduler.nextAt = now + rand(1200, tileFlashIntervalMs(scheduler.cluster.tile));
    }
    requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}
