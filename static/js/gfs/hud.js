import { jget, jpost, upload } from './api.js';
import { loadLocationVideos, renderVideoFrame } from './media.js';

export function createHud({ root, liveOverlay, liveVideo, onStartLive, onStopLive, onSelectLocation }) {
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
  el.close.onclick = () => { el.panel.classList.add('closed'); el.panel.setAttribute('aria-hidden', 'true'); };

  async function refresh() {
    if (!selected) return;
    const loc = await jget(`/gfs/api/location/${encodeURIComponent(selected.id)}`);
    el.title.textContent = loc.name;
    el.coords.textContent = `${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)}`;
    el.lastReport.textContent = (loc.reports?.slice(-1)[0]) || 'No reports yet';
    el.reports.innerHTML = '';
    (loc.reports || []).slice().reverse().forEach((r) => {
      const li = document.createElement('li');
      li.textContent = r;
      el.reports.appendChild(li);
    });
    const wx = await jget(`/gfs/api/weather?bbox=${loc.lon - 1},${loc.lat - 1},${loc.lon + 1},${loc.lat + 1}&quality=coarse`).catch(() => null);
    const bait = await jget(`/gfs/api/bait?bbox=${loc.lon - 1},${loc.lat - 1},${loc.lon + 1},${loc.lat + 1}&quality=coarse`).catch(() => null);
    el.conditions.textContent = wx ? `Valid: ${wx.valid_time || 'n/a'} stride:${wx.stride}` : 'Weather unavailable';
    el.weather.textContent = wx ? `Cloud field: ${Array.isArray(wx.fields?.cloud_cover) ? 'ready' : 'n/a'}` : 'n/a';
    el.bait.textContent = bait ? `Overall confidence: ${bait.confidence?.overall ?? 'n/a'}` : 'n/a';
    const vids = await loadLocationVideos(selected.id);
    renderVideoFrame(el.videoFrame, vids);
  }

  el.saveReport.onclick = async () => {
    if (!selected) return;
    const text = el.reportInput.value.trim();
    if (!text) return;
    await jpost(`/gfs/api/location/${encodeURIComponent(selected.id)}/reports`, { report: text });
    el.reportInput.value = '';
    await refresh();
  };

  el.uploadVideo.onclick = async () => {
    if (!selected || !el.uploadFile.files?.[0]) return;
    await upload(`/gfs/api/location/${encodeURIComponent(selected.id)}/upload`, el.uploadFile.files[0]);
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
