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

  function sendJson(type, extra = {}) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type, room, clientId, role: 'viewer', ...extra }));
  }

  function requestStream() {
    sendJson('request_stream');
  }

  function attachRemoteTrack(event) {
    const stream = event.streams && event.streams[0] ? event.streams[0] : new MediaStream([event.track]);
    v.srcObject = stream;
    v.playsInline = true;
    v.autoplay = true;
    v.muted = false;
    v.play().catch(() => {});
    standby.style.display = 'none';
    mode.textContent = 'LIVE';
  }

  function sendIceCandidate(candidate) {
    if (!candidate) return;
    sendJson('webrtc_ice', { candidate });
  }

  async function ensureViewerPeerConnection(force = false) {
    if (pc && !force) return pc;
    if (pc && force) { try { pc.close(); } catch {} }
    pc = new RTCPeerConnection({ iceServers: await iceServers() });
    pc.ontrack = attachRemoteTrack;
    pc.onicecandidate = (e) => sendIceCandidate(e.candidate);
    return pc;
  }

  function applyPresence(presence) {
    conn.textContent = `watchers ${presence.viewer_count ?? 0}`;
    if (presence.broadcaster_present === false && mode.textContent !== 'LIVE') {
      mode.textContent = 'STANDBY';
      standby.style.display = 'block';
    }
  }

  function updateAiStatus(status) {
    ai.textContent = `AI ${status || 'idle'}`;
  }

  async function handleWatchSocketMessage(msg) {
    if (msg.type === 'state_sync') {
      const st = msg.state || {};
      updateAiStatus(st.settings?.ai_status || (st.settings?.ai_enabled ? 'active' : 'idle'));
      const present = !!st.runtime?.broadcaster_present;
      if (present) requestStream();
      return;
    }
    if (msg.type === 'state_update') {
      const st = msg.state || {};
      updateAiStatus(st.settings?.ai_status || 'idle');
      const present = !!st.runtime?.broadcaster_present;
      if (present) requestStream();
      return;
    }
    if (msg.type === 'presence') {
      applyPresence(msg);
      if (msg.broadcaster_present) requestStream();
      return;
    }
    if (msg.type === 'ai_status') {
      updateAiStatus(msg.status || 'idle');
      return;
    }
    if (msg.type === 'error') {
      if (msg.message === 'no_broadcaster') {
        mode.textContent = 'OFFLINE';
        standby.style.display = 'block';
      }
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
    if ((msg.type === 'watch_offer' || msg.type === 'webrtc_offer') && msg.payload?.sdp) {
      const c = await ensureViewerPeerConnection(true);
      await c.setRemoteDescription(msg.payload);
      const answer = await c.createAnswer();
      await c.setLocalDescription(answer);
      sendJson('webrtc_answer', { sdp: answer.sdp, type: answer.type });
      return;
    }
    if (msg.type === 'webrtc_ice' && pc) {
      const cand = msg.candidate || msg.payload?.candidate;
      if (cand) {
        try { await pc.addIceCandidate(cand); } catch {}
      }
      return;
    }
    if (msg.type === 'webrtc_state' && msg.payload?.state !== 'connected' && mode.textContent === 'LIVE') {
      mode.textContent = 'STANDBY';
      standby.style.display = 'block';
      return;
    }
    if (msg.type === 'pong') return;
  }

  function connectWatchSocket() {
    const url = `${wsBase}/ws/watch`;
    console.info('[watch] websocket connect', { url, room });
    ws = new WebSocket(url);
    conn.textContent = 'connecting';
    ws.onopen = () => {
      retryDelayMs = 1000;
      conn.textContent = 'connected';
      sendJson('join');
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handleWatchSocketMessage(msg).catch((err) => console.warn('[watch] message handling failed', err));
    };
    ws.onerror = (err) => console.warn('[watch] websocket error', { url, room, err });
    ws.onclose = (ev) => {
      conn.textContent = 'reconnecting';
      console.warn('[watch] websocket closed', { url, room, code: ev?.code, reason: ev?.reason, retryDelayMs });
      setTimeout(connectWatchSocket, retryDelayMs);
      retryDelayMs = Math.min(15000, Math.round(retryDelayMs * 1.6));
    };
  }

  connectWatchSocket();
})();
