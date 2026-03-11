(function () {
  'use strict';

  const DEFAULTS = {
    minConfidenceFarZoom: 0.28,
    rebuildThrottleMs: 280,
    animationFloorMs: 16,
    maxActiveOrbs: 320,
  };

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, Number(v)));
  }

  function normalizeConfidence(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return 0.35;
    if (n > 1) return clamp(n / 100, 0, 1);
    return clamp(n, 0, 1);
  }

  function confidenceToOrbStyle(confidence, zoomScale = 1) {
    const c = normalizeConfidence(confidence);
    const brightness = clamp(0.2 + c * 0.8, 0, 1);
    const pulseSpeed = 0.5 + c * 2.5;
    const baseSize = 8 + c * 16;
    const orbScale = (0.75 + c * 1.25) * zoomScale;
    let palette = {
      core: [40, 120, 40],
      halo: [40, 120, 40],
      alpha: 0.35,
    };
    if (c >= 0.85) {
      palette = { core: [80, 255, 140], halo: [80, 255, 140], alpha: 0.9 };
    } else if (c >= 0.6) {
      palette = { core: [0, 255, 120], halo: [0, 255, 120], alpha: 0.75 };
    } else if (c >= 0.3) {
      palette = { core: [60, 220, 90], halo: [60, 220, 90], alpha: 0.55 };
    }
    return { confidence: c, brightness, pulseSpeed, baseSize, orbScale, palette };
  }

  function maxActiveOrbsForRange(range) {
    const r = Number(range) || 1800000;
    if (r > 4500000) return 90;
    if (r > 2600000) return 130;
    if (r > 1400000) return 190;
    if (r > 700000) return 240;
    return 320;
  }

  function clusterStepForRange(range) {
    const r = Number(range) || 1800000;
    if (r > 4500000) return 2.4;
    if (r > 2600000) return 1.4;
    if (r > 1400000) return 0.85;
    if (r > 700000) return 0.42;
    return 0.18;
  }

  function clusterFishLights(items, view, opts = {}) {
    const range = Number(view?.range) || 1800000;
    const bucketStep = clusterStepForRange(range);
    const maxOrbs = Math.min(DEFAULTS.maxActiveOrbs, maxActiveOrbsForRange(range));
    const minFar = Number(opts.minConfidenceFarZoom ?? DEFAULTS.minConfidenceFarZoom);
    const isFar = range > 2400000;
    const buckets = new Map();

    for (const item of items || []) {
      const lat = Number(item?.lat);
      const lon = Number(item?.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const confidence = normalizeConfidence(item?.confidence);
      if (isFar && confidence < minFar) continue;
      const by = Math.floor((lat + 90) / bucketStep);
      const bx = Math.floor((lon + 180) / bucketStep);
      const key = `${bx}:${by}`;
      const score = Math.max(0.01, confidence);
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = {
          key,
          latW: 0,
          lonW: 0,
          weight: 0,
          confidenceMax: 0,
          confidenceSum: 0,
          count: 0,
          top: item,
          ids: [],
        };
        buckets.set(key, bucket);
      }
      bucket.latW += lat * score;
      bucket.lonW += lon * score;
      bucket.weight += score;
      bucket.confidenceSum += confidence;
      bucket.confidenceMax = Math.max(bucket.confidenceMax, confidence);
      bucket.count += 1;
      if (!bucket.top || confidence > normalizeConfidence(bucket.top.confidence)) bucket.top = item;
      if (bucket.ids.length < 8) bucket.ids.push(item.id || item.location_key || item.name || `${lat},${lon}`);
    }

    const clustered = [];
    buckets.forEach((bucket) => {
      const cMean = bucket.count ? (bucket.confidenceSum / bucket.count) : bucket.confidenceMax;
      const confidence = clamp(Math.max(bucket.confidenceMax * 0.72, cMean), 0, 1);
      clustered.push({
        id: `cluster:${bucket.key}`,
        lat: bucket.weight ? bucket.latW / bucket.weight : 0,
        lon: bucket.weight ? bucket.lonW / bucket.weight : 0,
        altitude: 8,
        confidence,
        count: bucket.count,
        cluster: bucket.count > 1,
        point: bucket.top,
        summary: bucket.count > 1 ? `${bucket.count} fish signals • max ${(bucket.confidenceMax * 100).toFixed(0)}%` : (bucket.top?.name || bucket.top?.location_key || 'Fish signal'),
      });
    });

    clustered.sort((a, b) => {
      const scoreA = a.confidence * (a.cluster ? 1.18 : 1.0) * Math.log2(2 + a.count);
      const scoreB = b.confidence * (b.cluster ? 1.18 : 1.0) * Math.log2(2 + b.count);
      return scoreB - scoreA;
    });

    return clustered.slice(0, maxOrbs);
  }

  function createRenderer(ctx) {
    if (!ctx?.maps3dLib || !ctx?.markerLib) {
      throw new Error('fish orb renderer requires maps3dLib and markerLib');
    }
    const state = {
      rawItems: [],
      normalized: [],
      active: new Map(),
      free: [],
      selectedKey: null,
      enabled: true,
      rafId: null,
      lastFrameMs: 0,
      lastRebuildMs: 0,
      lastViewKey: '',
      rebuildMs: 0,
      lastClusterCount: 0,
      lastCandidatesCount: 0,
    };

    function normalizeItems(items) {
      const out = [];
      for (const item of items || []) {
        const lat = Number(item?.lat);
        const lon = Number(item?.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        const confidence = normalizeConfidence(item?.confidence ?? item?.bait?.confidence ?? item?.score ?? item?.intensity);
        out.push({
          id: item?.id || item?.location_key || item?.name || `${lat.toFixed(5)}:${lon.toFixed(5)}`,
          lat,
          lon,
          altitude: Number(item?.altitude ?? 10),
          confidence,
          intensity: item?.bait?.intensity || item?.intensity || (confidence > 0.6 ? 'high' : confidence > 0.3 ? 'medium' : 'low'),
          pulseSpeed: 0.5 + confidence * 2.5,
          size: 8 + confidence * 16,
          label: item?.name || item?.location_key || 'Fish orb',
          regionId: item?.tileId || item?.regionId || null,
          point: item,
        });
      }
      return out;
    }

    function viewKey(view) {
      if (!view) return 'unknown';
      return [
        Math.round((Number(view.lat) || 0) * 10) / 10,
        Math.round((Number(view.lon) || 0) * 10) / 10,
        Math.round((Number(view.range) || 0) / 50000),
        Math.round((Number(view.heading) || 0) / 8),
        Math.round((Number(view.tilt) || 0) / 8),
      ].join('|');
    }

    function buildPin(style, cluster) {
      return new ctx.markerLib.PinElement({
        scale: clamp(style.orbScale * (cluster ? 1.08 : 1), 0.7, 2.6),
        background: `rgba(${style.palette.core[0]},${style.palette.core[1]},${style.palette.core[2]},${clamp(0.35 + style.brightness * 0.55, 0.28, 0.95)})`,
        borderColor: 'rgba(220,252,231,0.95)',
        glyphColor: 'rgba(236,255,241,0.95)',
      });
    }

    function acquireOrb() {
      const pooled = state.free.pop();
      if (pooled) return pooled;
      const marker = new ctx.maps3dLib.Marker3DInteractiveElement({
        position: { lat: 0, lng: 0, altitude: 10 },
        title: 'Fish orb',
      });
      const style = confidenceToOrbStyle(0.5, 1);
      const pin = buildPin(style, false);
      marker.append(pin);
      const wrapper = {
        marker,
        pin,
        data: null,
        onClick: null,
        onEnter: null,
        onLeave: null,
        lastStyleKey: '',
      };
      return wrapper;
    }

    function releaseOrb(wrapper) {
      if (!wrapper) return;
      wrapper.data = null;
      try { wrapper.marker.remove(); } catch (_) {}
      state.free.push(wrapper);
    }

    function bindOrb(wrapper, data) {
      wrapper.data = data;
      wrapper.marker.position = { lat: data.lat, lng: data.lon, altitude: data.altitude || 10 };
      wrapper.marker.title = data.cluster ? `${data.count} fish signals` : (data.point?.name || data.point?.location_key || 'Fish orb');

      if (wrapper.onClick) {
        wrapper.marker.removeEventListener('gmp-click', wrapper.onClick);
        wrapper.marker.removeEventListener('click', wrapper.onClick);
        wrapper.marker.removeEventListener('mouseenter', wrapper.onEnter);
        wrapper.marker.removeEventListener('mouseleave', wrapper.onLeave);
      }

      wrapper.onClick = () => ctx.openHud(data.point || data, true);
      wrapper.onEnter = () => ctx.openHud(data.point || data, false);
      wrapper.onLeave = () => ctx.scheduleHudHide();
      wrapper.marker.addEventListener('gmp-click', wrapper.onClick);
      wrapper.marker.addEventListener('click', wrapper.onClick);
      wrapper.marker.addEventListener('mouseenter', wrapper.onEnter);
      wrapper.marker.addEventListener('mouseleave', wrapper.onLeave);

      ctx.globeEl.append(wrapper.marker);
    }

    function applyOrbVisual(wrapper, nowMs) {
      if (!wrapper?.data) return;
      const data = wrapper.data;
      const view = ctx.getView();
      const zoomScale = clamp(1.18 - ((Number(view?.range) || 1800000) / 6200000), 0.6, 1.35);
      const style = confidenceToOrbStyle(data.confidence, zoomScale);
      const t = nowMs / 1000;
      const pulse = 0.5 + 0.5 * Math.sin(t * style.pulseSpeed * Math.PI * 1.3);
      const pulseBoost = data.cluster ? 0.22 : 0.1;
      const pulseScale = 0.9 + pulse * pulseBoost;
      const isActive = state.selectedKey && (data.point?.location_key === state.selectedKey);
      const activeBoost = isActive ? 0.2 : 0;
      const scale = clamp(style.orbScale * pulseScale * (1 + activeBoost), 0.7, 2.8);
      const [r, g, b] = style.palette.halo;
      const key = `${Math.round(scale * 100)}:${r}:${g}:${b}:${Math.round(style.brightness * 100)}`;

      if (key !== wrapper.lastStyleKey) {
        wrapper.lastStyleKey = key;
        const bg = `rgba(${r},${g},${b},${clamp(0.35 + style.brightness * 0.55, 0.28, 0.95)})`;
        try {
          if (typeof wrapper.pin.scale !== 'undefined') wrapper.pin.scale = scale;
          if (typeof wrapper.pin.background !== 'undefined') wrapper.pin.background = bg;
          if (typeof wrapper.pin.borderColor !== 'undefined') wrapper.pin.borderColor = 'rgba(220,252,231,0.95)';
          if (typeof wrapper.pin.glyphColor !== 'undefined') wrapper.pin.glyphColor = 'rgba(236,255,241,0.95)';
        } catch (_) {
          try { wrapper.marker.removeChild(wrapper.pin); } catch (_) {}
          wrapper.pin = buildPin({ ...style, orbScale: scale }, data.cluster);
          wrapper.marker.append(wrapper.pin);
        }
      }

      const altBase = Number(data.altitude || 10);
      const altPulse = isActive ? (pulse * 2.8) : (pulse * 1.5);
      wrapper.marker.position = { lat: data.lat, lng: data.lon, altitude: altBase + altPulse };
    }

    function rebuild(force = false) {
      const now = performance.now();
      const view = ctx.getView();
      const vKey = viewKey(view);
      if (!force && (now - state.lastRebuildMs) < DEFAULTS.rebuildThrottleMs && vKey === state.lastViewKey) return false;
      const t0 = performance.now();
      state.lastViewKey = vKey;
      state.lastRebuildMs = now;
      const clustered = clusterFishLights(state.normalized, view, { minConfidenceFarZoom: DEFAULTS.minConfidenceFarZoom });
      state.lastCandidatesCount = state.normalized.length;
      state.lastClusterCount = clustered.length;

      const keep = new Set(clustered.map((c) => c.id));
      for (const [id, wrapper] of state.active.entries()) {
        if (keep.has(id)) continue;
        state.active.delete(id);
        releaseOrb(wrapper);
      }

      for (const c of clustered) {
        let wrapper = state.active.get(c.id);
        if (!wrapper) {
          wrapper = acquireOrb();
          state.active.set(c.id, wrapper);
        }
        bindOrb(wrapper, c);
      }

      state.rebuildMs = performance.now() - t0;
      return true;
    }

    function frame(nowMs) {
      if (!state.enabled) {
        state.rafId = null;
        return;
      }
      if ((nowMs - state.lastFrameMs) >= DEFAULTS.animationFloorMs) {
        state.lastFrameMs = nowMs;
        rebuild(false);
        state.active.forEach((wrapper) => applyOrbVisual(wrapper, nowMs));
      }
      state.rafId = requestAnimationFrame(frame);
    }

    return {
      setData(items) {
        state.rawItems = Array.isArray(items) ? items : [];
        state.normalized = normalizeItems(state.rawItems);
      },
      setSelectedKey(locationKey) {
        state.selectedKey = locationKey || null;
      },
      setEnabled(enabled) {
        state.enabled = !!enabled;
        if (!state.enabled) {
          this.stop();
          this.clear();
          return;
        }
        this.start();
      },
      rebuild(force = false) {
        return rebuild(!!force);
      },
      start() {
        if (state.rafId || !state.enabled) return;
        state.rafId = requestAnimationFrame(frame);
      },
      stop() {
        if (!state.rafId) return;
        cancelAnimationFrame(state.rafId);
        state.rafId = null;
      },
      clear() {
        for (const wrapper of state.active.values()) releaseOrb(wrapper);
        state.active.clear();
      },
      destroy() {
        this.stop();
        this.clear();
        state.free.length = 0;
      },
      getStats() {
        return {
          activeCount: state.active.size,
          freeCount: state.free.length,
          clusteredCount: state.lastClusterCount,
          candidateCount: state.lastCandidatesCount,
          rebuildMs: Number(state.rebuildMs || 0),
        };
      },
      debug: {
        confidenceToOrbStyle,
        clusterFishLights,
        maxActiveOrbsForRange,
      },
    };
  }

  window.LFTRFishOrbRenderer = {
    createRenderer,
    confidenceToOrbStyle,
    clusterFishLights,
    maxActiveOrbsForRange,
    normalizeConfidence,
  };
})();
