(() => {
  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsBase = `${wsProto}://${location.host}`;
  const room = (new URLSearchParams(location.search).get('room') || 'default').trim() || 'default';
  const clientId = `w-${Math.random().toString(36).slice(2, 10)}`;

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

  function send(type, extra = {}) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type, room, clientId, role: 'viewer', ...extra }));
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
    pc.onicecandidate = (e) => { if (e.candidate) send('webrtc_ice', { candidate: e.candidate }); };
    return pc;
  }

  async function handleMessage(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    if (msg.type === 'state_sync') {
      const st = msg.state || {};
      ai.textContent = st.settings?.ai_status ? `AI ${st.settings.ai_status}` : (st.settings?.ai_enabled ? 'AI on' : 'AI off');
      return;
    }

    if (msg.type === 'presence') {
      conn.textContent = `watchers ${msg.viewer_count ?? 0}`;
      return;
    }

    if (msg.type === 'stage_state') {
      const p = msg.payload || {};
      label.textContent = p.label || 'PUBLIC ACCESS';
      if (p.mode === 'upload' && p.latestUploadUrl) {
        v.srcObject = null;
        v.src = p.latestUploadUrl;
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
      send('watch_answer', { sdp: answer.sdp, type: answer.type });
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
      send('join');
    };
    ws.onmessage = (ev) => handleMessage(ev.data);
    ws.onerror = (err) => console.warn('[watch] websocket error', { url, room, err });
    ws.onclose = (ev) => {
      conn.textContent = 'reconnecting';
      console.warn('[watch] websocket closed', { url, room, code: ev?.code, reason: ev?.reason, retryDelayMs });
      setTimeout(connect, retryDelayMs);
      retryDelayMs = Math.min(15000, Math.round(retryDelayMs * 1.6));
    };
  }

  connect();
})();
