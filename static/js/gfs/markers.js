const MARKER_DEBUG = Boolean(window.__GFS_DEBUG);

function resolveProbability(loc) {
  const value = Number(loc?.probability ?? loc?.confidence ?? 1);
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 1;
}

function parseCoordinate(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function hashPhaseSeed(input) {
  const text = String(input || 'fish-orb');
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 1000) / 1000;
}

function greenForProbability(probability) {
  if (probability >= 0.66) return '#39ff78';
  if (probability >= 0.33) return '#58f592';
  return '#43d97a';
}

function orbSizeForProbability(probability) {
  return 42 + Math.round(probability * 8);
}

function pulseEnvelope(probability, phase, seconds) {
  const omega = Math.PI * 1.2;
  const smooth = (Math.sin(seconds * omega + phase) + 1) * 0.5;

  if (probability >= 0.66) {
    const crest = Math.pow(smooth, 5) * 0.26;
    const snap = Math.exp(-Math.pow((smooth - 0.97) / 0.055, 2)) * 0.32;
    return 0.34 + (smooth * 0.46) + crest + snap;
  }
  if (probability >= 0.33) {
    const crest = Math.pow(smooth, 3) * 0.2;
    return 0.3 + (smooth * 0.5) + crest;
  }
  return 0.28 + (smooth * 0.44);
}

function createOrbTemplate(probability) {
  const color = greenForProbability(probability);
  const size = orbSizeForProbability(probability);
  const uid = `orb-${Math.random().toString(36).slice(2, 10)}`;
  const icon = document.createElement('div');
  icon.slot = 'icon';
  icon.innerHTML = `
    <svg width="${size}" height="${size}" viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" style="pointer-events:none">
      <defs>
        <radialGradient id="${uid}-core" cx="30%" cy="28%" r="70%">
          <stop offset="0%" stop-color="#dcffe9"/>
          <stop offset="42%" stop-color="${color}"/>
          <stop offset="100%" stop-color="#0a3c22"/>
        </radialGradient>
        <radialGradient id="${uid}-halo" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.62"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </radialGradient>
      </defs>
      <circle data-role="halo" cx="22" cy="22" r="19" fill="url(#${uid}-halo)"/>
      <circle data-role="core" cx="22" cy="22" r="12.5" fill="url(#${uid}-core)"/>
      <ellipse data-role="highlight" cx="17.5" cy="15" rx="4.5" ry="2.6" fill="white" fill-opacity="0.82" transform="rotate(-24 17.5 15)"/>
    </svg>`;

  const core = icon.querySelector('[data-role="core"]');
  const halo = icon.querySelector('[data-role="halo"]');
  const highlight = icon.querySelector('[data-role="highlight"]');
  return { icon, core, halo, highlight };
}

function createFishMarker({ maps3d, loc, altitudeMode }) {
  const probability = resolveProbability(loc);
  const marker = new maps3d.Marker3DInteractiveElement({
    position: { lat: loc.lat, lng: loc.lon, altitude: 18 },
    altitudeMode,
    title: loc.name || loc.title || 'Fishing location',
    drawsWhenOccluded: false,
    sizePreserved: true,
    extruded: false,
  });

  const orb = createOrbTemplate(probability);
  const pin = document.createElement('gmp-pin-3d');
  pin.append(orb.icon);
  marker.append(pin);

  if (MARKER_DEBUG) {
    console.debug('[gfs markers] created svg orb marker', { id: loc?.id, probability });
  }

  return { marker, probability, orb };
}

function startPulseLoop(animatedOrbs) {
  let frameId = null;
  const tick = (now) => {
    const seconds = now * 0.001;
    for (const orb of animatedOrbs) {
      if (!orb?.core || !orb?.halo || !orb?.highlight) continue;
      const glow = pulseEnvelope(orb.probability, orb.phase, seconds);
      const coreOpacity = Math.min(1, 0.5 + glow * 0.55);
      const haloOpacity = Math.min(1, 0.22 + glow * 0.7);
      const highlightOpacity = Math.min(1, 0.34 + glow * 0.55);

      orb.core.setAttribute('fill-opacity', coreOpacity.toFixed(3));
      orb.halo.setAttribute('fill-opacity', haloOpacity.toFixed(3));
      orb.highlight.setAttribute('fill-opacity', highlightOpacity.toFixed(3));
    }
    frameId = requestAnimationFrame(tick);
  };
  frameId = requestAnimationFrame(tick);
  return () => {
    if (frameId) cancelAnimationFrame(frameId);
  };
}

export function renderMarkers({ locations, globeEl, maps3d, onSelect }) {
  const active = [];
  const animatedOrbs = [];
  const altitudeMode = maps3d?.AltitudeMode?.RELATIVE_TO_GROUND || 'RELATIVE_TO_GROUND';

  for (const loc of locations) {
    const lat = parseCoordinate(loc?.lat);
    const lon = parseCoordinate(loc?.lon);
    if (lat === null || lon === null) {
      console.warn('[gfs markers] skipped invalid location coordinates', { id: loc?.id, lat: loc?.lat, lon: loc?.lon });
      continue;
    }

    const normalizedLoc = { ...loc, lat, lon };
    let built;
    try {
      built = createFishMarker({ maps3d, loc: normalizedLoc, altitudeMode });
    } catch (err) {
      console.error('[gfs markers] failed creating marker', { id: loc?.id, error: String(err) });
      continue;
    }

    const click = () => {
      if (MARKER_DEBUG) {
        console.debug('[gfs markers] click -> HUD open', { id: normalizedLoc?.id });
      }
      onSelect(normalizedLoc);
    };

    built.marker.addEventListener('gmp-click', click);
    built.marker.addEventListener('click', click);

    globeEl.append(built.marker);
    active.push(built.marker);

    const phaseSeed = `${normalizedLoc.id || normalizedLoc.name || ''}:${lat.toFixed(4)}:${lon.toFixed(4)}`;
    animatedOrbs.push({
      probability: built.probability,
      phase: hashPhaseSeed(phaseSeed) * Math.PI * 2,
      core: built.orb.core,
      halo: built.orb.halo,
      highlight: built.orb.highlight,
    });
  }

  const stopPulse = startPulseLoop(animatedOrbs);

  return () => {
    stopPulse();
    active.forEach((marker) => {
      try { marker.remove(); } catch (_) {}
    });
  };
}
