import { getJsonSafe, postJsonSafe, uploadSafe } from './api.js';
import { loadLocationVideos, renderVideoFrame } from './media.js';

function gridAverage(grid, fallback = null) {
  if (!Array.isArray(grid) || !Array.isArray(grid[0])) return fallback;
  const arr = Array.isArray(grid[0][0]) ? grid[0] : grid;
  let sum = 0;
  let count = 0;
  arr.forEach((row) => {
    if (!Array.isArray(row)) return;
    row.forEach((v) => {
      const n = Number(v);
      if (Number.isFinite(n)) {
        sum += n;
        count += 1;
      }
    });
  });
  return count ? (sum / count) : fallback;
}

function pct(value, digits = 0) {
  if (!Number.isFinite(Number(value))) return 'n/a';
  return `${Number(value).toFixed(digits)}%`;
}

export function createHud({ root, liveOverlay, liveVideo, onStartLive, onStopLive, onSelectLocation, getOverlaySummary }) {
  const el = {
    panel: root,
    close: document.getElementById('hudClose'),
    title: document.getElementById('hudLocation'),
    coords: document.getElementById('hudCoords'),
    conditions: document.getElementById('hudConditions'),
    videoFrame: document.getElementById('hudVideoFrame'),
    lastReport: document.getElementById('hudLastReport'),
    reportInput: document.getElementById('hudReportInput'),
    saveReport: document.getElementById('hudSaveReport'),
    uploadFile: document.getElementById('hudUploadFile'),
    uploadVideo: document.getElementById('hudUploadVideo'),
    goLive: document.getElementById('hudGoLive'),
    stopLive: document.getElementById('hudStopLive'),
    weather: document.getElementById('hudWeather'),
    bait: document.getElementById('hudBait'),
    reports: document.getElementById('hudReports'),
  };

  let selected = null;
  el.close.onclick = () => {
    el.panel.classList.add('closed');
    el.panel.setAttribute('aria-hidden', 'true');
  };

  async function refresh() {
    if (!selected) return;
    const loc = await getJsonSafe(`/gfs/api/location/${encodeURIComponent(selected.id)}`, null);
    if (!loc) {
      el.conditions.textContent = 'Location unavailable';
      return;
    }

    el.title.textContent = loc.name || selected.name;
    el.coords.textContent = `${Number(loc.lat || selected.lat).toFixed(4)}, ${Number(loc.lon || selected.lon).toFixed(4)}`;
    el.lastReport.textContent = (loc.reports?.slice(-1)[0]) || 'No reports yet';
    el.reports.innerHTML = '';
    (loc.reports || []).slice().reverse().forEach((r) => {
      const li = document.createElement('li');
      li.textContent = r;
      el.reports.appendChild(li);
    });

    const box = `${(loc.lon - 0.6).toFixed(4)},${(loc.lat - 0.6).toFixed(4)},${(loc.lon + 0.6).toFixed(4)},${(loc.lat + 0.6).toFixed(4)}`;
    const [wx, bait, clouds] = await Promise.all([
      getJsonSafe(`/gfs/api/weather?bbox=${box}&quality=coarse`, null),
      getJsonSafe(`/gfs/api/bait?bbox=${box}&quality=coarse`, null),
      getJsonSafe(`/gfs/api/clouds?bbox=${box}&quality=coarse`, null),
    ]);

    const localOverlay = typeof getOverlaySummary === 'function' ? getOverlaySummary(loc) : null;
    const localCloud = Number(localOverlay?.cloudCover);
    const localRain = Number(localOverlay?.rainRate);
    const localBait = Number(localOverlay?.baitOverall);

    const cloudAvg = Number.isFinite(localCloud)
      ? localCloud
      : gridAverage(wx?.fields?.cloud_cover, gridAverage(clouds?.cloud_layers?.find((l) => l?.name === 'low')?.density, NaN));
    const rainAvg = Number.isFinite(localRain) ? localRain : gridAverage(wx?.fields?.prate, NaN);
    const baitOverall = Number.isFinite(localBait) ? localBait : Number(bait?.confidence?.overall ?? NaN);
    const validTime = localOverlay?.validTime || wx?.valid_time || clouds?.valid_time || bait?.valid_time || 'n/a';

    const rainText = Number.isFinite(rainAvg) ? (rainAvg > 0.12 ? 'active rain' : rainAvg > 0.02 ? 'light precip' : 'dry') : 'n/a';
    el.conditions.textContent = `Cloud ${pct(cloudAvg)} • ${rainText} • valid ${validTime}`;
    el.weather.textContent = `Cloud cover: ${pct(cloudAvg)} • Rain rate: ${Number.isFinite(rainAvg) ? rainAvg.toFixed(3) : 'n/a'} • Weather ${wx ? 'ready' : 'n/a'}`;
    el.bait.textContent = `Bait confidence: ${Number.isFinite(baitOverall) ? baitOverall.toFixed(2) : 'n/a'} • Front lines: ${(bait?.front_lines || []).length}`;

    const vids = await loadLocationVideos(selected.id);
    renderVideoFrame(el.videoFrame, vids);
  }

  el.saveReport.onclick = async () => {
    if (!selected) return;
    const text = el.reportInput.value.trim();
    if (!text) return;
    await postJsonSafe(`/gfs/api/location/${encodeURIComponent(selected.id)}/reports`, { report: text }, null);
    el.reportInput.value = '';
    await refresh();
  };

  el.uploadVideo.onclick = async () => {
    if (!selected || !el.uploadFile.files?.[0]) return;
    await uploadSafe(`/gfs/api/location/${encodeURIComponent(selected.id)}/upload`, el.uploadFile.files[0], {}, null);
    await refresh();
  };

  el.goLive.onclick = () => selected && onStartLive(selected);
  el.stopLive.onclick = () => selected && onStopLive(selected);

  return {
    async open(location) {
      selected = location;
      if (onSelectLocation) onSelectLocation(location);
      el.panel.classList.remove('closed');
      el.panel.setAttribute('aria-hidden', 'false');
      await refresh();
    },
    selected: () => selected,
  };
}
