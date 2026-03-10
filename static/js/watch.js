(() => {
  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsBase = `${wsProto}://${location.host}`;
  const room = (new URLSearchParams(location.search).get('room') || 'default').trim() || 'default';

  const v = document.getElementById('v');
  const standby = document.getElementById('standby');
  const conn = document.getElementById('conn');
  const mode = document.getElementById('mode');
  const ai = document.getElementById('ai');
  const label = document.getElementById('label');

  let ws = null;
  let pc = null;
  let retryDelayMs = 1000;

  async function iceServers() {
    try {
      const r = await fetch('/webrtc/ice-config');
      const j = await r.json();
      return j.iceServers || [{ urls: 'stun:stun.l.google.com:19302' }];
    } catch {
      return [{ urls: 'stun:stun.l.google.com:19302' }];
    }
  }

  function send(type, payload) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type, payload }));
  }

  async function ensurePc(force = false) {
    if (pc && !force) return pc;
    if (pc && force) { try { pc.close(); } catch {} }
    pc = new RTCPeerConnection({ iceServers: await iceServers() });
    pc.ontrack = (ev) => {
      v.srcObject = ev.streams[0];
      standby.style.display = 'none';
      mode.textContent = 'LIVE';
    };
    pc.onicecandidate = (e) => { if (e.candidate) send('watch_ice', { candidate: e.candidate, room }); };
    return pc;
  }

  async function handleMessage(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    if (msg.type === 'room_status') {
      ai.textContent = msg.payload?.ai?.enabled ? 'AI on' : 'AI off';
      return;
    }
    if (msg.type === 'stage_state') {
      label.textContent = msg.payload?.label || 'PUBLIC ACCESS';
      if (msg.payload?.mode === 'upload' && msg.payload?.latestUploadUrl) {
        v.srcObject = null;
        v.src = msg.payload.latestUploadUrl;
        v.play().catch(() => {});
        standby.style.display = 'none';
        mode.textContent = 'LATEST UPLOAD';
      }
      return;
    }
    if (msg.type === 'watch_offer' && msg.payload?.sdp) {
      const c = await ensurePc(true);
      await c.setRemoteDescription(msg.payload);
      const answer = await c.createAnswer();
      await c.setLocalDescription(answer);
      send('watch_answer', { sdp: answer.sdp, type: answer.type, room });
      return;
    }
    if (msg.type === 'webrtc_ice' && msg.payload?.candidate && pc) {
      try { await pc.addIceCandidate(msg.payload.candidate); } catch {}
      return;
    }
    if (msg.type === 'webrtc_state' && msg.payload?.state !== 'connected' && mode.textContent === 'LIVE') {
      mode.textContent = 'STANDBY';
      standby.style.display = 'block';
    }
  }

  function connect() {
    const url = `${wsBase}/ws/watch`;
    console.info('[watch] websocket connect', { url, room });
    ws = new WebSocket(url);
    conn.textContent = 'connecting';
    ws.onopen = () => {
      retryDelayMs = 1000;
      conn.textContent = 'connected';
      send('watch_join', { room });
    };
    ws.onmessage = (ev) => handleMessage(ev.data);
    ws.onerror = (err) => {
      console.warn('[watch] websocket error', { url, room, err });
    };
    ws.onclose = (ev) => {
      conn.textContent = 'reconnecting';
      console.warn('[watch] websocket closed', { url, room, code: ev?.code, reason: ev?.reason, retryDelayMs });
      setTimeout(connect, retryDelayMs);
      retryDelayMs = Math.min(15000, Math.round(retryDelayMs * 1.6));
    };
  }

  connect();
})();
