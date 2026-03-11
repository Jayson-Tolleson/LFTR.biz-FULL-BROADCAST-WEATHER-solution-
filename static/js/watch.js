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
  const videoWrap = v?.closest('.videoWrap') || v?.parentElement;

  let ws = null;
  let pc = null;
  let retryDelayMs = 1000;
  let requestPending = false;
  let broadcasterPresent = false;
  let needsStreamRequest = false;
  let streamAttached = false;
  let isNegotiating = false;
  let hasRequestedStream = false;

  const unmuteBtn = document.createElement('button');
  unmuteBtn.type = 'button';
  unmuteBtn.textContent = 'Tap for sound';
  unmuteBtn.style.cssText = 'position:absolute;right:12px;bottom:12px;z-index:4;padding:8px 10px;border-radius:999px;border:1px solid #2f3b57;background:rgba(9,13,25,.82);color:#e8eefc;cursor:pointer;display:none';
  if (videoWrap) {
    const pos = getComputedStyle(videoWrap).position;
    if (!pos || pos === 'static') videoWrap.style.position = 'relative';
    videoWrap.appendChild(unmuteBtn);
  }

  function showUnmute(show) {
    unmuteBtn.style.display = show ? 'inline-flex' : 'none';
  }

  async function playVideo(reason) {
    try {
      await v.play();
    } catch (err) {
      console.warn('[watch] video play blocked', { reason, message: err?.message || String(err) });
    }
  }

  function setLiveMutedAutoplay() {
    v.playsInline = true;
    v.autoplay = true;
    v.muted = true;
    showUnmute(true);
  }

  unmuteBtn.addEventListener('click', async () => {
    v.muted = false;
    await playVideo('manual_unmute');
    showUnmute(false);
  });

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

  function requestStream(force = false) {
    const connected = !!(pc && pc.connectionState === 'connected' && streamAttached);
    if (!force && (!broadcasterPresent || requestPending || isNegotiating || connected || hasRequestedStream)) {
      needsStreamRequest = !broadcasterPresent;
      return;
    }
    requestPending = true;
    hasRequestedStream = true;
    needsStreamRequest = false;
    sendJson('request_stream');
  }

  function attachRemoteTrack(event) {
    const stream = event.streams && event.streams[0] ? event.streams[0] : new MediaStream([event.track]);
    v.srcObject = stream;
    streamAttached = true;
    setLiveMutedAutoplay();
    playVideo('remote_track_attach');
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
      broadcasterPresent = present;
      if (present) {
        needsStreamRequest = false;
        requestStream();
      }
      return;
    }
    if (msg.type === 'state_update') {
      const st = msg.state || {};
      updateAiStatus(st.settings?.ai_status || 'idle');
      const present = !!st.runtime?.broadcaster_present;
      broadcasterPresent = present;
      if (present && !requestPending) {
        needsStreamRequest = false;
        requestStream();
      }
      return;
    }
    if (msg.type === 'presence') {
      applyPresence(msg);
      broadcasterPresent = !!msg.broadcaster_present;
      if (broadcasterPresent && needsStreamRequest) {
        requestStream();
        needsStreamRequest = false;
      } else if (!broadcasterPresent) {
        requestPending = false;
        hasRequestedStream = false;
        needsStreamRequest = true;
      }
      return;
    }
    if (msg.type === 'ai_status') {
      updateAiStatus(msg.status || 'idle');
      return;
    }
    if (msg.type === 'waiting' || msg.type === 'error') {
      if (msg.message === 'no_broadcaster' || msg.message === 'stream_offline') {
        requestPending = false;
        needsStreamRequest = true;
        hasRequestedStream = false;
        mode.textContent = 'OFFLINE';
        standby.style.display = 'block';
      }
      return;
    }
    if (msg.type === 'stage_state') {
      const p = msg.payload || {};
      label.textContent = p.label || 'PUBLIC ACCESS';
      if (p.mode === 'upload' && p.latestUploadUrl) {
        streamAttached = false;
        showUnmute(false);
        v.srcObject = null;
        v.src = p.latestUploadUrl;
        playVideo('fallback_upload');
        standby.style.display = 'none';
        mode.textContent = 'LATEST UPLOAD';
      }
      return;
    }
    if ((msg.type === 'watch_offer' || msg.type === 'webrtc_offer') && msg.payload?.sdp) {
      isNegotiating = true;
      try {
        const c = await ensureViewerPeerConnection(true);
        await c.setRemoteDescription(msg.payload);
        const answer = await c.createAnswer();
        await c.setLocalDescription(answer);
        sendJson('webrtc_answer', { sdp: answer.sdp, type: answer.type });
      } finally {
        requestPending = false;
        hasRequestedStream = false;
        isNegotiating = false;
      }
      return;
    }
    if (msg.type === 'webrtc_ice' && pc) {
      const cand = msg.candidate || msg.payload?.candidate;
      if (cand) {
        try { await pc.addIceCandidate(cand); } catch {}
      }
      return;
    }
    if (msg.type === 'webrtc_state') {
      const st = msg.payload?.state;
      if (st === 'connected') {
        isNegotiating = false;
        requestPending = false;
        hasRequestedStream = false;
        return;
      }
      if ((st === 'closed' || st === 'failed' || st === 'disconnected') && mode.textContent === 'LIVE') {
        streamAttached = false;
        isNegotiating = false;
        requestPending = false;
        hasRequestedStream = false;
        mode.textContent = 'STANDBY';
        standby.style.display = 'block';
        if (broadcasterPresent) requestStream();
      }
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
      requestPending = false;
      isNegotiating = false;
      hasRequestedStream = false;
      needsStreamRequest = true;
      sendJson('join');
      if (broadcasterPresent) requestStream();
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handleWatchSocketMessage(msg).catch((err) => console.warn('[watch] message handling failed', err));
    };
    ws.onerror = (err) => console.warn('[watch] websocket error', { url, room, err });
    ws.onclose = (ev) => {
      conn.textContent = 'reconnecting';
      requestPending = false;
      isNegotiating = false;
      hasRequestedStream = false;
      streamAttached = false;
      console.warn('[watch] websocket closed', { url, room, code: ev?.code, reason: ev?.reason, retryDelayMs });
      setTimeout(connectWatchSocket, retryDelayMs);
      retryDelayMs = Math.min(20000, Math.round(retryDelayMs * 1.8));
    };
  }

  connectWatchSocket();
})();
