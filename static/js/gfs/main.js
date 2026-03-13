import { getJsonSafe, uploadSafe } from './api.js';
import { ensureMaps3D, libs } from './globe.js';
import { renderMarkers } from './markers.js';
import { createHud } from './hud.js';
import { startLive, stopLive } from './live.js';
import { renderBaitZones } from './bait-zones.js';
import { renderCloudZones } from './cloud-zones.js';
import { renderRainZones } from './rain-zones.js';
import { createLiveOverlay, destroyLiveOverlay } from '../ui/liveOverlay.js';

const statusEl = document.getElementById('status');
const globeEl = document.getElementById('globe');
const fallbackEl = document.getElementById('globeFallback');
const pillClouds = document.getElementById('pillClouds');
const pillRain = document.getElementById('pillRain');
const pillBait = document.getElementById('pillBait');
const pillJetstream = document.getElementById('pillJetstream');

let activeLocation = null;
let liveManuallyDismissed = false;
let selectedLocation = null;
let liveStatePollId = null;
const GFS_DEBUG = Boolean(window.__GFS_DEBUG);

const overlayState = {
  cloudsEnabled: true,
  rainEnabled: true,
  baitEnabled: true,
  jetstreamEnabled: true,
  cleanupClouds: null,
  cleanupRain: null,
  cleanupBait: null,
  cleanupJetstream: null,
  lastSignature: '',
  inFlight: false,
  pending: false,
  requestSeq: 0,
  activeAbort: null,
  latest: { weather: null, clouds: null, baitBase: null, baitAdvanced: null, bbox: null },
  pendingBuffer: null,
  lastRenderReason: 'boot',
};

function showStatus(text) {
  statusEl.textContent = text;
}

function hideLiveOverlay() {
  destroyLiveOverlay();
}

function showLiveOverlay() {
  if (liveManuallyDismissed) return;
  createLiveOverlay({
    muted: true,
    onClose: async () => {
      liveManuallyDismissed = true;
      if (activeLocation) await stopLive({ locationId: activeLocation.id });
    },
  });
}

function currentLiveOverlayRefs() {
  const overlayEl = document.getElementById('liveOverlay');
  const videoEl = document.getElementById('livePreview');
  return { overlayEl, videoEl };
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

function parseCenter() {
  const raw = globeEl.getAttribute('center') || '';
  const parts = raw.split(',').map((x) => Number(x.trim()));
  if (parts.length >= 2 && Number.isFinite(parts[0]) && Number.isFinite(parts[1])) {
    return { lat: parts[0], lon: parts[1] };
  }
  return { lat: 34.2, lon: -120 };
}

function parseRangeMeters() {
  const attr = Number(globeEl.getAttribute('range'));
  if (Number.isFinite(attr) && attr > 0) return attr;
  return 1800000;
}


function parseBoundsAttr(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const parts = raw.split(',').map((x) => Number(x.trim()));
  if (parts.length < 4 || parts.some((n) => !Number.isFinite(n))) return null;
  return { west: parts[0], south: parts[1], east: parts[2], north: parts[3] };
}

function trueViewportFromGlobeBounds() {
  const attrCandidates = [
    globeEl.getAttribute('bounds'),
    globeEl.getAttribute('view-bounds'),
    globeEl.getAttribute('visible-bounds'),
  ];
  for (const raw of attrCandidates) {
    const parsed = parseBoundsAttr(raw);
    if (parsed) return parsed;
  }
  if (globeEl && typeof globeEl.getBounds === 'function') {
    try {
      const b = globeEl.getBounds();
      if (b && Number.isFinite(b.west) && Number.isFinite(b.south) && Number.isFinite(b.east) && Number.isFinite(b.north)) {
        return { west: b.west, south: b.south, east: b.east, north: b.north };
      }
    } catch (_) {}
  }
  return null;
}

function getCanonicalViewport() {
  const c = parseCenter();
  const range = parseRangeMeters();
  const fromBounds = trueViewportFromGlobeBounds();
  if (fromBounds) {
    return {
      west: Math.max(-179.9, fromBounds.west),
      south: Math.max(-89.9, fromBounds.south),
      east: Math.min(179.9, fromBounds.east),
      north: Math.min(89.9, fromBounds.north),
      quality: 'coarse',
      camera: { center: c, range, source: 'visible_bounds' },
    };
  }

  const latSpan = Math.max(1.2, Math.min(24, range / 75000));
  const lonSpan = Math.min(36, latSpan / Math.max(Math.cos((c.lat * Math.PI) / 180), 0.35));
  const south = Math.max(-89.9, c.lat - latSpan / 2);
  const north = Math.min(89.9, c.lat + latSpan / 2);
  const west = Math.max(-179.9, c.lon - lonSpan / 2);
  const east = Math.min(179.9, c.lon + lonSpan / 2);
  return {
    west,
    south,
    east,
    north,
    quality: 'coarse',
    camera: { center: c, range, source: 'camera_heuristic' },
  };
}

function bboxToQuery(b) {
  return `${b.west.toFixed(4)},${b.south.toFixed(4)},${b.east.toFixed(4)},${b.north.toFixed(4)}`;
}

function viewportToQuery(viewport) {
  return encodeURIComponent(JSON.stringify(viewport));
}

function bboxSignature(b) {
  const range = parseRangeMeters();
  return `${b.west.toFixed(2)}:${b.south.toFixed(2)}:${b.east.toFixed(2)}:${b.north.toFixed(2)}:${Math.round(range / 25000)}`;
}

function updatePillVisuals() {
  pillClouds?.classList.toggle('active', overlayState.cloudsEnabled);
  pillRain?.classList.toggle('active', overlayState.rainEnabled);
  pillBait?.classList.toggle('active', overlayState.baitEnabled);
  pillJetstream?.classList.toggle('active', overlayState.jetstreamEnabled);
}

function clearLayers() {
  overlayState.cleanupClouds?.();
  overlayState.cleanupRain?.();
  overlayState.cleanupBait?.();
  overlayState.cleanupJetstream?.();
  overlayState.cleanupClouds = null;
  overlayState.cleanupRain = null;
  overlayState.cleanupBait = null;
  overlayState.cleanupJetstream = null;
}

function renderJetstreamLayer() {
  if (typeof window.drawJetBalloons !== 'function') return () => {};
  window.drawJetBalloons().catch((err) => console.warn('[gfs jetstream] render failed', err?.message || err));
  return () => {
    if (typeof window.clearJetBalloons === 'function') {
      window.clearJetBalloons();
    }
  };
}

function renderOverlays(reason = 'manual') {
  overlayState.lastRenderReason = reason;
  overlayState.cleanupClouds?.();

  if (overlayState.cloudsEnabled) {
    overlayState.cleanupClouds = renderCloudZones({ payload: overlayState.latest.clouds, map3DElement: globeEl });
  } else {
    overlayState.cleanupClouds = null;
  }

  const allowHeavyDraw = reason === 'steady';
  if (allowHeavyDraw) {
    const buffered = overlayState.pendingBuffer;
    const weatherPayload = buffered?.weather || overlayState.latest.weather;
    const baitPayload = buffered?.baitAdvanced || overlayState.latest.baitAdvanced;

    overlayState.cleanupRain?.();
    overlayState.cleanupBait?.();

    if (overlayState.rainEnabled) {
      overlayState.cleanupRain = renderRainZones({ payload: weatherPayload, map3DElement: globeEl, viewportReason: reason });
    } else {
      overlayState.cleanupRain = null;
    }

    if (overlayState.baitEnabled) {
      overlayState.cleanupBait = renderBaitZones({ payload: baitPayload, map3DElement: globeEl, viewportReason: reason });
    } else {
      overlayState.cleanupBait = null;
    }

    overlayState.pendingBuffer = null;
  } else {
    overlayState.pendingBuffer = {
      weather: overlayState.latest.weather,
      baitAdvanced: overlayState.latest.baitAdvanced,
    };
    console.info('[gfs overlays] heavy layers deferred', { reason });
  }

  if (overlayState.jetstreamEnabled) {
    overlayState.cleanupJetstream = renderJetstreamLayer();
  } else {
    overlayState.cleanupJetstream = null;
  }
}

async function refreshOverlays(reason = 'manual') {
  const viewport = getCanonicalViewport();
  console.info('[gfs overlays] viewport', { viewport, reason });
  const b = viewport;
  const signature = bboxSignature(b);

  if (!overlayState.pending && overlayState.lastSignature === signature && reason !== 'toggle') {
    if (reason === 'steady' && overlayState.pendingBuffer) {
      renderOverlays('steady');
    }
    return;
  }

  if (overlayState.inFlight && overlayState.activeAbort) {
    try { overlayState.activeAbort.abort(); } catch (_) {}
  }

  const seq = overlayState.requestSeq + 1;
  overlayState.requestSeq = seq;
  overlayState.inFlight = true;
  overlayState.pending = false;
  const controller = new AbortController();
  overlayState.activeAbort = controller;

  const expectedSignature = signature;
  const fetchAdvanced = async () => {
    try {
      const bboxQ = encodeURIComponent(bboxToQuery(b));
      const vpQ = viewportToQuery(viewport);
      const baitAdvanced = await getJsonSafe(`/gfs/api/bait/advanced?bbox=${bboxQ}&viewport=${vpQ}&quality=${viewport.quality}`, null, { signal: controller.signal });
      if (seq !== overlayState.requestSeq || expectedSignature !== overlayState.lastSignature) {
        if (GFS_DEBUG) console.debug('[gfs overlays] stale advanced response discarded', { seq, latest: overlayState.requestSeq });
        return;
      }
      if (baitAdvanced) {
        overlayState.latest.baitAdvanced = baitAdvanced;
        renderOverlays(reason);
        if (GFS_DEBUG) console.debug('[gfs overlays] advanced bait replaced base', { seq });
      }
    } catch (err) {
      if (err?.name !== 'AbortError') {
        console.warn('[gfs overlays] advanced bait fetch failed', err?.message || err);
      }
    }
  };

  try {
    const bboxQ = encodeURIComponent(bboxToQuery(b));
    window.__gfsLastBbox = bboxToQuery(b);
    const vpQ = viewportToQuery(viewport);
    const req = (path) => getJsonSafe(path, null, { signal: controller.signal });
    const [weather, clouds, baitBase] = await Promise.all([
      req(`/gfs/api/weather?bbox=${bboxQ}&viewport=${vpQ}&quality=${viewport.quality}`),
      req(`/gfs/api/clouds?bbox=${bboxQ}&viewport=${vpQ}&quality=${viewport.quality}`),
      req(`/gfs/api/bait?bbox=${bboxQ}&viewport=${vpQ}&quality=${viewport.quality}`),
    ]);

    if (seq !== overlayState.requestSeq) {
      if (GFS_DEBUG) console.debug('[gfs overlays] stale response discarded', { seq, latest: overlayState.requestSeq });
      return;
    }

    overlayState.latest = { weather, clouds, baitBase, baitAdvanced: null, bbox: b };
    overlayState.lastSignature = signature;
    renderOverlays(reason);
    console.info('[gfs overlays] refreshed', {
      reason,
      signature,
      seq,
      clouds: Boolean(clouds),
      rain: Boolean(weather),
      baitBase: Boolean(baitBase),
    });

    fetchAdvanced();
  } catch (err) {
    if (err?.name === 'AbortError') {
      if (GFS_DEBUG) console.debug('[gfs overlays] request aborted', { reason, seq });
    } else {
      console.warn('[gfs overlays] refresh failed', err?.message || err);
    }
  } finally {
    if (overlayState.activeAbort === controller) {
      overlayState.activeAbort = null;
    }
    if (seq === overlayState.requestSeq) {
      overlayState.inFlight = false;
    }
    if (overlayState.pending && !overlayState.inFlight) {
      overlayState.pending = false;
      refreshOverlays('pending');
    }
  }
}

function installSteadyRefresh() {
  let dirty = true;

  const onMove = () => {
    dirty = true;
  };

  const onSteady = (ev) => {
    const isSteady = ev?.isSteady;
    if (typeof isSteady === 'boolean' && !isSteady) return;
    if (!dirty) return;
    dirty = false;
    refreshOverlays('steady');
  };

  ['gmp-centerchange', 'gmp-headingchange', 'gmp-rangechange', 'gmp-rollchange', 'gmp-tiltchange', 'gmp-camerapositionchange'].forEach((evt) => {
    globeEl.addEventListener(evt, onMove);
  });
  globeEl.addEventListener('gmp-steadystate', onSteady);
  globeEl.addEventListener('gmp-steadychange', onSteady);

  return () => {
    ['gmp-centerchange', 'gmp-headingchange', 'gmp-rangechange', 'gmp-rollchange', 'gmp-tiltchange', 'gmp-camerapositionchange'].forEach((evt) => {
      globeEl.removeEventListener(evt, onMove);
    });
    globeEl.removeEventListener('gmp-steadystate', onSteady);
    globeEl.removeEventListener('gmp-steadychange', onSteady);
  };
}


function installTimerRefresh() {
  const id = setInterval(() => {
    if (!document.hidden) refreshOverlays('timer');
  }, 90000);
  return () => clearInterval(id);
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
    ws = new WebSocket(`${proto}//${location.host}/ws/gfs`);

    ws.onopen = () => {
      connecting = false;
      backoffMs = 1000;
      clearTimers();
      startHeartbeat();
      console.info('[gfs/ws] connected');
    };
    ws.onmessage = (ev) => {
      lastMessageAt = Date.now();
      try { handleMessage(JSON.parse(ev.data)); } catch (_) {}
    };
    ws.onerror = () => console.warn('[gfs/ws] socket error');
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

function nearestOverlaySummary(loc) {
  const bbox = overlayState.latest.bbox;
  if (!bbox) return null;
  const weather = overlayState.latest.weather;
  const clouds = overlayState.latest.clouds;
  const bait = overlayState.latest.baitAdvanced || overlayState.latest.baitBase;
  const lat = Number(loc?.lat);
  const lon = Number(loc?.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;

  const sample = (grid) => {
    if (!Array.isArray(grid) || !Array.isArray(grid[0])) return null;
    const arr = Array.isArray(grid[0][0]) ? grid[0] : grid;
    const ny = arr.length;
    const nx = Array.isArray(arr[0]) ? arr[0].length : 0;
    if (!ny || !nx) return null;
    const yi = Math.max(0, Math.min(ny - 1, Math.floor(((lat - bbox.south) / (bbox.north - bbox.south || 1)) * ny)));
    const xi = Math.max(0, Math.min(nx - 1, Math.floor(((lon - bbox.west) / (bbox.east - bbox.west || 1)) * nx)));
    return Number(arr[yi]?.[xi]);
  };

  return {
    validTime: weather?.valid_time || clouds?.valid_time || bait?.valid_time || null,
    cloudCover: sample(weather?.fields?.cloud_cover),
    rainRate: sample(weather?.fields?.prate),
    lowCloud: sample(clouds?.cloud_layers?.find((l) => l?.name === 'low')?.density),
    baitOverall: Number(bait?.confidence?.overall ?? NaN),
  };
}

function sampleWeatherAt(lat, lon) {
  const bbox = overlayState.latest.bbox;
  const weather = overlayState.latest.weather;
  if (!bbox || !weather) return null;
  const sample = (grid) => {
    if (!Array.isArray(grid) || !Array.isArray(grid[0])) return NaN;
    const arr = Array.isArray(grid[0][0]) ? grid[0] : grid;
    const ny = arr.length;
    const nx = Array.isArray(arr[0]) ? arr[0].length : 0;
    if (!ny || !nx) return NaN;
    const yi = Math.max(0, Math.min(ny - 1, Math.floor(((lat - bbox.south) / (bbox.north - bbox.south || 1)) * ny)));
    const xi = Math.max(0, Math.min(nx - 1, Math.floor(((lon - bbox.west) / (bbox.east - bbox.west || 1)) * nx)));
    return Number(arr[yi]?.[xi]);
  };
  const windU = sample(weather?.fields?.wind_u);
  const windV = sample(weather?.fields?.wind_v);
  const tempK = sample(weather?.fields?.temp2m);
  const pressurePa = sample(weather?.fields?.mslp);
  return {
    temperature_c: Number.isFinite(tempK) ? (tempK - 273.15) : NaN,
    pressure_hpa: Number.isFinite(pressurePa) ? (pressurePa / 100) : NaN,
    wind_speed_mps: Number.isFinite(windU) && Number.isFinite(windV) ? Math.hypot(windU, windV) : NaN,
  };
}

const hud = createHud({
  root: document.getElementById('locationHud'),
  getOverlaySummary: nearestOverlaySummary,
  onSelectLocation: (loc) => {
    selectedLocation = loc;
    refreshSelectedLiveState();
  },
  onStartLive: async (loc) => {
    activeLocation = loc;
    selectedLocation = loc;
    liveManuallyDismissed = false;
    showLiveOverlay();
    const refs = currentLiveOverlayRefs();
    await startLive({ locationId: loc.id, videoEl: refs.videoEl, overlayEl: refs.overlayEl });
    showStatus(`Live started: ${loc.name}`);
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
    showStatus(`Live stopped: ${loc.name}`);
    await hud.open(loc);
  },
});

function installHoverHud() {
  const handler = (ev) => {
    const d = ev?.detail || {};
    const lat = Number(d?.latLng?.lat ?? d?.position?.lat ?? d?.lat);
    const lon = Number(d?.latLng?.lng ?? d?.position?.lng ?? d?.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const sample = sampleWeatherAt(lat, lon);
    hud.updateHover({ lat, lon }, sample);
    if (typeof window.updateHUD === 'function') {
      window.updateHUD(sample);
    }
  };
  globeEl.addEventListener('gmp-click', handler);
  globeEl.addEventListener('gmp-pointermove', handler);
  return () => {
    globeEl.removeEventListener('gmp-click', handler);
    globeEl.removeEventListener('gmp-pointermove', handler);
  };
}


window.addEventListener('beforeunload', () => {
  stopLivePolling();
  gfsSocket.close();
  clearLayers();
});

async function boot() {
  hideLiveOverlay();
  updatePillVisuals();

  const ready = await ensureMaps3D(globeEl, fallbackEl);
  if (!ready.ok) {
    showStatus(`Globe unavailable (${ready.reason})`);
    return;
  }

  const { maps3d } = await libs();
  const payload = await getJsonSafe('/gfs/api/locations', { locations: [] });
  const locations = payload?.locations || [];
  renderMarkers({ locations, globeEl, maps3d, onSelect: (loc) => hud.open(loc) });

  const teardownSteady = installSteadyRefresh();
  const teardownTimerRefresh = installTimerRefresh();
  const teardownHoverHud = installHoverHud();

  pillClouds?.addEventListener('click', () => {
    overlayState.cloudsEnabled = !overlayState.cloudsEnabled;
    updatePillVisuals();
    refreshOverlays('toggle');
  });
  pillRain?.addEventListener('click', () => {
    overlayState.rainEnabled = !overlayState.rainEnabled;
    updatePillVisuals();
    refreshOverlays('toggle');
  });
  pillBait?.addEventListener('click', () => {
    overlayState.baitEnabled = !overlayState.baitEnabled;
    updatePillVisuals();
    refreshOverlays('toggle');
  });
  pillJetstream?.addEventListener('click', () => {
    overlayState.jetstreamEnabled = !overlayState.jetstreamEnabled;
    updatePillVisuals();
    refreshOverlays('toggle');
  });

  await refreshOverlays('boot');

  showStatus(`Ready • ${locations.length} fish beacons`);
  startLivePolling();
  gfsSocket.connect();

  window.addEventListener('beforeunload', teardownSteady, { once: true });
  window.addEventListener('beforeunload', teardownTimerRefresh, { once: true });
  window.addEventListener('beforeunload', teardownHoverHud, { once: true });
}

boot().catch((e) => {
  showStatus(`Error: ${e.message}`);
  console.error(e);
});
