import {
  buildCloudCluster,
  flashCloudCluster,
  getCloudClusters,
  resetCloudClusters,
  shiftCluster
} from './volumetric-clouds.js';
import { startRainShaftSystem } from './rain-shafts.js';
import { startLightningSystem } from './lightning.js';
import { startCloudAdvection } from './cloud-advection.js';

const MAX_TILES = 250;

function expandTile(tile) {
  if (typeof tile.coverage !== 'undefined' && typeof tile.density !== 'undefined') return tile;
  return {
    lat: tile.lat,
    lng: tile.lng,
    coverage: tile.c,
    base_altitude_m: tile.b,
    top_altitude_m: tile.t,
    density: tile.d,
    precip_rate: tile.p,
    storm_energy: tile.s,
    wind_u: tile.u,
    wind_v: tile.v,
    importance: tile.i
  };
}

export async function renderCloudLayer(globe) {
  resetCloudClusters();

  const resp = await fetch('/gfs/api/cloud_tiles?limit=250&compact=1');
  const payload = await resp.json();
  const tiles = (payload.items || []).slice(0, MAX_TILES).map(expandTile);

  for (const tile of tiles) {
    const cluster = buildCloudCluster(tile, globe, window.BROADCAST.layers.clouds);
    window.BROADCAST.cloudTiles.push(cluster.tile);
  }

  const clusters = getCloudClusters();
  startRainShaftSystem(globe, clusters.map((c) => c.tile));
  startLightningSystem(globe, clusters, flashCloudCluster);
  startCloudAdvection(globe, clusters, shiftCluster);
}
