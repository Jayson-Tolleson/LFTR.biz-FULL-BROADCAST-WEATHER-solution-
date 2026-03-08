import { renderVolumetricClouds, startCloudAdvection } from './volumetric-clouds.js';
import { renderJetstreamRibbons } from './jetstream.js';
import { startRainShafts } from './rain-shafts.js';
import { startLightning } from './lightning.js';
import { renderSwellVectors } from '../marine/swell-vectors.js';
import { renderBaitSchools } from '../marine/fish-school-renderer.js';
import { initHUD } from '../hud/hud.js';
import { mountBroadcastPopup } from '../broadcast/broadcast-popup.js';

async function loadMapsScript() {
  const cfg = await (await fetch('/gfs/api/config')).json();
  await new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = `https://maps.googleapis.com/maps/api/js?key=${cfg.googleMapsApiKey}&v=beta&libraries=maps3d,marker`;
    s.async = true;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
}

async function loadLayers(globe) {
  const cloudTilesResp = await fetch('/gfs/api/cloud_tiles?limit=300&compact=1');
  const cloudTiles = (await cloudTilesResp.json()).items;
  const expanded = cloudTiles.map((t) => ({ lat: t.lat, lng: t.lng, base_altitude_m: t.b, top_altitude_m: t.t, coverage: t.c, density: t.d, precip_rate: t.p, storm_energy: t.s, wind_u: t.u, wind_v: t.v }));

  const clusters = renderVolumetricClouds(globe, expanded);
  startRainShafts(globe, clusters);
  startLightning(globe, clusters);
  startCloudAdvection(globe, clusters);

  await renderJetstreamRibbons(globe);
  await renderSwellVectors(globe);
  await renderBaitSchools(globe);
}

async function boot() {
  initHUD();
  mountBroadcastPopup();
  await loadMapsScript();
  const globe = document.getElementById('globe');
  globe.center = { lat: 24, lng: -35, altitude: 17500000 };
  window.BROADCAST.globe = globe;

  globe.addEventListener('gmp-steadystate', async () => {
    if (window.BROADCAST.mapReady) return;
    window.BROADCAST.mapReady = true;
    await loadLayers(globe);
  });
}

boot().catch(console.error);
