import { renderCloudLayer } from './clouds.js';
import { renderRainLayer } from './rain.js';
import { renderBalloons } from './balloons.js';
import { renderFish } from './fish.js';
import { renderJetStreams } from './jetstream.js';
import { renderOceanSwell } from './ocean-swell.js';
import { initHUD } from './hud.js';

async function loadMapsScript() {
  const configResp = await fetch('/gfs/api/config');
  const conf = await configResp.json();
  const key = conf.googleMapsApiKey || '';

  await new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `https://maps.googleapis.com/maps/api/js?key=${key}&v=beta&libraries=maps3d,marker`;
    script.async = true;
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

async function loadWeatherLayers() {
  const globe = window.BROADCAST.globe;
  await Promise.all([
    renderCloudLayer(globe),
    renderRainLayer(globe),
    renderBalloons(globe),
    renderFish(globe),
    renderJetStreams(globe),
    renderOceanSwell(globe)
  ]);
}

async function boot() {
  initHUD();
  await loadMapsScript();

  const globe = document.getElementById('globe');
  globe.center = { lat: 20, lng: -30, altitude: 18000000 };
  window.BROADCAST.globe = globe;

  globe.addEventListener('gmp-steadystate', async () => {
    if (window.BROADCAST.mapReady) return;
    window.BROADCAST.mapReady = true;
    await loadWeatherLayers();
  });
}

boot().catch((err) => {
  console.error('Failed to initialize globe', err);
});
