import { jget, upload } from './api.js';
import { ensureMaps3D, libs } from './globe.js';
import { renderMarkers } from './markers.js';
import { createHud } from './hud.js';
import { startLive, stopLive } from './live.js';

const statusEl = document.getElementById('status');
const globeEl = document.getElementById('globe');
const fallbackEl = document.getElementById('globeFallback');
const liveOverlay = document.getElementById('liveOverlay');
const liveVideo = document.getElementById('livePreview');
const liveClose = document.getElementById('liveClose');
const liveMute = document.getElementById('liveMute');

let activeLocation = null;

const hud = createHud({
  root: document.getElementById('locationHud'),
  liveOverlay,
  liveVideo,
  onStartLive: async (loc) => {
    activeLocation = loc;
    await startLive({ locationId: loc.id, videoEl: liveVideo, overlayEl: liveOverlay });
    statusEl.textContent = `Live started: ${loc.name}`;
  },
  onStopLive: async (loc) => {
    await stopLive({ locationId: loc.id, onBlob: async (blob) => {
      const f = new File([blob], `live-${Date.now()}.webm`, { type: 'video/webm' });
      await upload(`/gfs/api/location/${encodeURIComponent(loc.id)}/upload`, f);
    }});
    liveOverlay.classList.add('hidden');
    statusEl.textContent = `Live stopped: ${loc.name}`;
    await hud.open(loc);
  },
});

liveClose.onclick = async () => {
  if (activeLocation) {
    await stopLive({ locationId: activeLocation.id });
    liveOverlay.classList.add('hidden');
  }
};
liveMute.onclick = () => { liveVideo.muted = !liveVideo.muted; liveMute.textContent = liveVideo.muted ? 'Unmute' : 'Mute'; };

async function boot() {
  const ready = await ensureMaps3D(globeEl, fallbackEl);
  if (!ready) { statusEl.textContent = 'Globe unavailable'; return; }
  const { maps3d, marker } = await libs();
  const payload = await jget('/gfs/api/locations');
  renderMarkers({ locations: payload.locations || [], globeEl, maps3d, marker, onSelect: (loc) => hud.open(loc) });
  statusEl.textContent = `Ready • ${(payload.locations || []).length} fish beacons`;
}

boot().catch((e) => { statusEl.textContent = `Error: ${e.message}`; console.error(e); });
