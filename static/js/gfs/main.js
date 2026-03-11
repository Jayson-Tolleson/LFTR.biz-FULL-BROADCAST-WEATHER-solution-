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
let liveManuallyDismissed = false;
let selectedLocation = null;

function hideLiveOverlay() {
  liveOverlay.classList.add('hidden');
}

function showLiveOverlay() {
  if (liveManuallyDismissed) return;
  liveOverlay.classList.remove('hidden');
}

async function refreshSelectedLiveState() {
  if (!selectedLocation) return;
  try {
    const payload = await jget(`/gfs/api/location/${encodeURIComponent(selectedLocation.id)}/live`);
    if (payload?.live?.active) showLiveOverlay();
    else hideLiveOverlay();
  } catch {
    // keep current state on transient failures
  }
}

const hud = createHud({
  root: document.getElementById('locationHud'),
  liveOverlay,
  liveVideo,
  onSelectLocation: (loc) => {
    selectedLocation = loc;
    refreshSelectedLiveState();
  },
  onStartLive: async (loc) => {
    activeLocation = loc;
    selectedLocation = loc;
    liveManuallyDismissed = false;
    await startLive({ locationId: loc.id, videoEl: liveVideo, overlayEl: liveOverlay });
    showLiveOverlay();
    statusEl.textContent = `Live started: ${loc.name}`;
  },
  onStopLive: async (loc) => {
    await stopLive({
      locationId: loc.id,
      onBlob: async (blob) => {
        const f = new File([blob], `live-${Date.now()}.webm`, { type: 'video/webm' });
        await upload(`/gfs/api/location/${encodeURIComponent(loc.id)}/upload`, f);
      },
    });
    hideLiveOverlay();
    statusEl.textContent = `Live stopped: ${loc.name}`;
    await hud.open(loc);
  },
});

liveClose.onclick = async () => {
  liveManuallyDismissed = true;
  if (activeLocation) {
    await stopLive({ locationId: activeLocation.id });
  }
  hideLiveOverlay();
};

liveMute.onclick = () => {
  liveVideo.muted = !liveVideo.muted;
  liveMute.textContent = liveVideo.muted ? 'Unmute' : 'Mute';
};

async function boot() {
  hideLiveOverlay();
  const ready = await ensureMaps3D(globeEl, fallbackEl);
  if (!ready.ok) {
    statusEl.textContent = `Globe unavailable (${ready.reason})`;
    return;
  }
  const { maps3d } = await libs();
  const payload = await jget('/gfs/api/locations');
  renderMarkers({ locations: payload.locations || [], globeEl, maps3d, onSelect: (loc) => hud.open(loc) });
  statusEl.textContent = `Ready • ${(payload.locations || []).length} fish beacons`;
  setInterval(refreshSelectedLiveState, 12000);
}

boot().catch((e) => {
  statusEl.textContent = `Error: ${e.message}`;
  console.error(e);
});
