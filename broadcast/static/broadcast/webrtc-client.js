let localStream = null;
const streamId = `stream-${Date.now()}`;

async function start() {
  localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
  document.getElementById('preview').srcObject = localStream;
  const pos = await new Promise((resolve) => navigator.geolocation.getCurrentPosition(resolve, () => resolve({ coords: { latitude: 0, longitude: 0 } })));
  const body = { stream_id: streamId, lat: pos.coords.latitude, lng: pos.coords.longitude };
  const res = await fetch('/broadcast/api/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  document.getElementById('status').textContent = JSON.stringify(await res.json(), null, 2);
}

async function stop() {
  if (localStream) localStream.getTracks().forEach((t) => t.stop());
  const res = await fetch('/broadcast/api/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stream_id: streamId }) });
  document.getElementById('status').textContent = JSON.stringify(await res.json(), null, 2);
}

document.getElementById('start').addEventListener('click', start);
document.getElementById('stop').addEventListener('click', stop);
