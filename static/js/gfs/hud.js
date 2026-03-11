import { getJsonSafe, postJsonSafe, uploadSafe } from './api.js';
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
    const wx = await getJsonSafe(`/gfs/api/weather?bbox=${loc.lon - 1},${loc.lat - 1},${loc.lon + 1},${loc.lat + 1}&quality=coarse`, null);
    const bait = await getJsonSafe(`/gfs/api/bait?bbox=${loc.lon - 1},${loc.lat - 1},${loc.lon + 1},${loc.lat + 1}&quality=coarse`, null);
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
