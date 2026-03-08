import { baitHudHtml } from './bait-hud.js';
import { locationHudHtml } from './location-hud.js';
import { updateVideoPane } from '../broadcast/video-pane.js';

export function initHUD() {
  const hud = document.getElementById('hud');
  hud.innerHTML = `<h2>Global Fishing Intelligence Network</h2>
    <div id="loc-panel"></div>
    <div id="bait-panel"></div>
    <div id="forecast-panel"><h3>BAIT FORECAST</h3><div>Loading…</div></div>
    <div id="video-panel"><h3>LIVE REPORT</h3><div id="video-status">No stream selected.</div></div>`;

  document.getElementById('loc-panel').innerHTML = locationHudHtml(null);
  document.getElementById('bait-panel').innerHTML = baitHudHtml(null);

  window.setInterval(async () => {
    if (!window.BROADCAST.selectedLocation) return;
    await updateVideoPane(document.getElementById('video-status'), window.BROADCAST.selectedLocation.lat, window.BROADCAST.selectedLocation.lng);
  }, 5000);
}
