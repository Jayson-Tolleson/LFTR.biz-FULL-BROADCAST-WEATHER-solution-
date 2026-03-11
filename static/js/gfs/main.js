import { getJsonSafe, uploadSafe } from './api.js';
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
let liveStatePollId = null;

function hideLiveOverlay() {
  liveOverlay.classList.add('hidden');
}

function showLiveOverlay() {
  if (liveManuallyDismissed) return;
  liveOverlay.classList.remove('hidden');
}

async function refreshSelectedLiveState() {
  if (!selectedLocation) return;
  const payload = await getJsonSafe(`/gfs/api/location/${encodeURIComponent(selectedLocation.id)}/live`, null);
  if (!payload) return;
  if (payload?.live?.active) showLiveOverlay();
  else hideLiveOverlay();
}

function startLivePolling() {
  if (liveStatePollId) return;
  liveStatePollId = setInterval(refreshSelectedLiveState, 12000);
}

function stopLivePolling() {
  if (!liveStatePollId) return;
  clearInterval(liveStatePollId);
  liveStatePollId = null;
}

function createGfsSocket() {
  let ws = null;
  let reconnectTimer = null;
  let pingTimer = null;
  let watchdogTimer = null;
  let backoffMs = 1000;
  let manualClose = false;
  let connecting = false;
  let lastMessageAt = 0;

  const clearTimers = () => {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (pingTimer) clearInterval(pingTimer);
    if (watchdogTimer) clearInterval(watchdogTimer);
    reconnectTimer = null;
    pingTimer = null;
    watchdogTimer = null;
  };

  const scheduleReconnect = () => {
    if (manualClose || reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, backoffMs);
    backoffMs = Math.min(15000, backoffMs * 2);
  };

  const startHeartbeat = () => {
    lastMessageAt = Date.now();
    pingTimer = setInterval(() => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      try { ws.send(JSON.stringify({ type: 'ping' })); } catch (_) {}
    }, 20000);

    watchdogTimer = setInterval(() => {
      const stale = Date.now() - lastMessageAt > 30000;
      if (stale && ws && ws.readyState === WebSocket.OPEN) {
        console.warn('[gfs/ws] inactivity timeout, reconnecting');
        try { ws.close(4000, 'inactivity timeout'); } catch (_) {}
      }
    }, 5000);
  };

  const handleMessage = (msg) => {
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'snapshot_changed' && msg.detail?.location_id) {
      if (selectedLocation && msg.detail.location_id === selectedLocation.id) {
        if (msg.detail.active) showLiveOverlay();
        else hideLiveOverlay();
      }
    }
  };

  const connect = () => {
    if (manualClose || connecting || (ws && ws.readyState === WebSocket.OPEN)) return;
    connecting = true;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/gfs`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      connecting = false;
      backoffMs = 1000;
      clearTimers();
      startHeartbeat();
      console.info('[gfs/ws] connected');
    };

    ws.onmessage = (ev) => {
      lastMessageAt = Date.now();
      try {
        const msg = JSON.parse(ev.data);
        handleMessage(msg);
      } catch (_) {
        // ignore non-json ws frames
      }
    };

    ws.onerror = () => {
      console.warn('[gfs/ws] socket error');
    };

    ws.onclose = () => {
      connecting = false;
      clearTimers();
      if (!manualClose) scheduleReconnect();
    };
  };

  const close = () => {
    manualClose = true;
    clearTimers();
    if (ws) {
      try { ws.close(1000, 'page unload'); } catch (_) {}
      ws = null;
    }
  };

  return { connect, close };
}

const gfsSocket = createGfsSocket();

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
        await uploadSafe(`/gfs/api/location/${encodeURIComponent(loc.id)}/upload`, f, {}, null);
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

window.addEventListener('beforeunload', () => {
  stopLivePolling();
  gfsSocket.close();
});

async function boot() {
  hideLiveOverlay();
  const ready = await ensureMaps3D(globeEl, fallbackEl);
  if (!ready.ok) {
    statusEl.textContent = `Globe unavailable (${ready.reason})`;
    return;
  }
  const { maps3d } = await libs();
  const payload = await getJsonSafe('/gfs/api/locations', { locations: [] });
  renderMarkers({ locations: payload?.locations || [], globeEl, maps3d, onSelect: (loc) => hud.open(loc) });
  statusEl.textContent = `Ready • ${(payload?.locations || []).length} fish beacons`;
  startLivePolling();
  gfsSocket.connect();
}

boot().catch((e) => {
  statusEl.textContent = `Error: ${e.message}`;
  console.error(e);
});
