const VOXEL_X = 16;
const VOXEL_Y = 16;
const VOXEL_Z = 8;
const MAX_BILLOWS_PER_TILE = 60;

const cloudClusters = [];

function fract(v) {
  return v - Math.floor(v);
}

function smoothStep(t) {
  return t * t * (3 - 2 * t);
}

function rand3(x, y, z) {
  return fract(Math.sin((x * 127.1) + (y * 311.7) + (z * 74.7)) * 43758.5453123);
}

function valueNoise3(x, y, z) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const z0 = Math.floor(z);
  const x1 = x0 + 1;
  const y1 = y0 + 1;
  const z1 = z0 + 1;

  const tx = smoothStep(x - x0);
  const ty = smoothStep(y - y0);
  const tz = smoothStep(z - z0);

  const c000 = rand3(x0, y0, z0);
  const c100 = rand3(x1, y0, z0);
  const c010 = rand3(x0, y1, z0);
  const c110 = rand3(x1, y1, z0);
  const c001 = rand3(x0, y0, z1);
  const c101 = rand3(x1, y0, z1);
  const c011 = rand3(x0, y1, z1);
  const c111 = rand3(x1, y1, z1);

  const x00 = c000 + (c100 - c000) * tx;
  const x10 = c010 + (c110 - c010) * tx;
  const x01 = c001 + (c101 - c001) * tx;
  const x11 = c011 + (c111 - c011) * tx;
  const y0i = x00 + (x10 - x00) * ty;
  const y1i = x01 + (x11 - x01) * ty;
  return y0i + (y1i - y0i) * tz;
}

function fbmNoise3(x, y, z) {
  let amp = 0.5;
  let freq = 1.0;
  let sum = 0.0;
  for (let i = 0; i < 4; i++) {
    sum += valueNoise3(x * freq, y * freq, z * freq) * amp;
    amp *= 0.5;
    freq *= 2.03;
  }
  return Math.max(0, Math.min(1, sum / 0.9375));
}

function cloudRegime(tile) {
  if (tile.top_altitude_m > 9000) return 'CUMULONIMBUS';
  if (tile.coverage > 0.72) return 'STRATOCUMULUS';
  return 'CUMULUS';
}

function resolveLayerCount(globe) {
  const altitude = globe?.center?.altitude ?? 18000000;
  if (altitude > 6000000) return 6;
  if (altitude > 900000) return 10;
  return 14;
}

function generateVoxelField(tile, layers) {
  const zRes = Math.max(VOXEL_Z, layers);
  const grid = [];

  for (let x = 0; x < VOXEL_X; x++) {
    for (let y = 0; y < VOXEL_Y; y++) {
      for (let z = 0; z < zRes; z++) {
        const noise = fbmNoise3(tile.lat + x * 0.02, tile.lng + y * 0.02, z * 0.15);
        const heightFactor = 1.0 - Math.abs((z / (zRes - 1 || 1)) - 0.55);
        grid.push({
          x,
          y,
          z,
          density: Math.max(0, Math.min(1, noise * tile.density * (0.55 + heightFactor)))
        });
      }
    }
  }

  return { grid, zRes };
}

function generateBillowCircle(lat, lng, radiusDeg, altitude) {
  const pts = [];
  for (let i = 0; i < 12; i++) {
    const angle = (i / 12) * Math.PI * 2;
    pts.push({
      lat: lat + Math.cos(angle) * radiusDeg,
      lng: lng + Math.sin(angle) * radiusDeg,
      altitude
    });
  }
  return pts;
}

function pickBillowVoxels(grid, zRes, tile) {
  const accepted = [];
  const threshold = tile.coverage > 0.75 ? 0.34 : 0.42;
  for (const voxel of grid) {
    if (voxel.density <= threshold) continue;
    const zNormalized = voxel.z / (zRes - 1 || 1);
    accepted.push({ ...voxel, weight: voxel.density * (0.35 + zNormalized * 0.65) });
  }
  accepted.sort((a, b) => b.weight - a.weight);
  return accepted.slice(0, MAX_BILLOWS_PER_TILE);
}

function buildCloudCluster(tile, globe, cloudLayerStore) {
  const layers = resolveLayerCount(globe);
  const { grid, zRes } = generateVoxelField(tile, layers);
  const regime = cloudRegime(tile);
  const altitudeRange = Math.max(300, tile.top_altitude_m - tile.base_altitude_m);
  const voxels = pickBillowVoxels(grid, zRes, tile);

  const elements = [];
  for (const voxel of voxels) {
    const layerFactor = voxel.z / (zRes - 1 || 1);
    const alpha = 0.35 + (layerFactor * 0.25);
    const latOffset = (voxel.x - (VOXEL_X / 2)) * 0.024;
    const lngOffset = (voxel.y - (VOXEL_Y / 2)) * 0.024;

    const noiseScale = 1 + (fbmNoise3(tile.lat + voxel.x, tile.lng + voxel.y, voxel.z) - 0.5) * 0.25;
    let radius = (0.028 + tile.coverage * 0.04 + voxel.density * 0.03) * noiseScale;
    if (regime === 'CUMULONIMBUS' && layerFactor > 0.72) radius *= 2.5;
    if (regime === 'STRATOCUMULUS') radius *= 1.25;

    const altitude = tile.base_altitude_m + (layerFactor * altitudeRange) + ((rand3(voxel.x, voxel.y, voxel.z) - 0.5) * 240);
    const fillColor = `rgba(255,255,255,${Math.min(0.7, alpha)})`;

    const patch = new google.maps.maps3d.Polygon3DElement({
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      fillColor,
      strokeWidth: 0,
      outerCoordinates: generateBillowCircle(tile.lat + latOffset, tile.lng + lngOffset, radius, altitude)
    });

    patch.__baseFillColor = fillColor;
    globe.append(patch);
    cloudLayerStore.push(patch);
    elements.push(patch);
  }

  const fog = new google.maps.maps3d.Polygon3DElement({
    altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
    fillColor: 'rgba(230,236,246,0.12)',
    strokeWidth: 0,
    outerCoordinates: generateBillowCircle(tile.lat, tile.lng, 0.35 + tile.coverage * 0.22, Math.max(150, tile.base_altitude_m * 0.12))
  });
  fog.__baseFillColor = 'rgba(230,236,246,0.12)';
  globe.append(fog);
  cloudLayerStore.push(fog);
  elements.push(fog);

  const cluster = {
    tile,
    regime,
    layers,
    elements,
    center: { lat: tile.lat, lng: tile.lng, altitude: (tile.base_altitude_m + tile.top_altitude_m) * 0.5 }
  };
  cloudClusters.push(cluster);
  return cluster;
}

function colorFromAlpha(alpha) {
  return `rgba(255,255,255,${Math.max(0.1, Math.min(0.95, alpha))})`;
}

function flashCloudCluster(cluster, amount = 0.3, durationMs = 120) {
  for (const element of cluster.elements) {
    const base = element.__baseFillColor || 'rgba(255,255,255,0.4)';
    const alpha = Number((base.match(/rgba\([^,]+,[^,]+,[^,]+,([^)]+)\)/) || [])[1] || 0.4);
    element.fillColor = colorFromAlpha(alpha + amount);
  }

  window.setTimeout(() => {
    for (const element of cluster.elements) {
      if (element.__baseFillColor) element.fillColor = element.__baseFillColor;
    }
  }, durationMs);
}

function shiftCluster(cluster, dLat, dLng, windShear = 1.0) {
  cluster.center.lat += dLat;
  cluster.center.lng += dLng;
  cluster.tile.lat += dLat;
  cluster.tile.lng += dLng;

  for (const element of cluster.elements) {
    const coords = element.outerCoordinates || [];
    if (!coords.length) continue;
    const avgAlt = coords.reduce((acc, p) => acc + (p.altitude || 0), 0) / coords.length;
    const lower = cluster.tile.base_altitude_m;
    const upper = cluster.tile.top_altitude_m;
    const t = Math.max(0, Math.min(1, (avgAlt - lower) / Math.max(1, (upper - lower))));
    const layerWind = (0.8 + t * 0.4) * windShear;
    const moved = coords.map((pt) => ({ ...pt, lat: pt.lat + dLat * layerWind, lng: pt.lng + dLng * layerWind }));
    element.outerCoordinates = moved;
  }
}

function getCloudClusters() {
  return cloudClusters;
}

function resetCloudClusters() {
  cloudClusters.length = 0;
}

export {
  buildCloudCluster,
  cloudRegime,
  flashCloudCluster,
  generateBillowCircle,
  generateVoxelField,
  getCloudClusters,
  resolveLayerCount,
  resetCloudClusters,
  shiftCluster
};
