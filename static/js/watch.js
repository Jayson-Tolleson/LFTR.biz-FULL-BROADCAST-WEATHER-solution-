(() => {
  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsBase = `${wsProto}://${location.host}`;
  const room = (new URLSearchParams(location.search).get('room') || 'default').trim() || 'default';

  const dom = {
    video: document.getElementById('remoteVideo'),
    standby: document.getElementById('standby'),
    statusText: document.getElementById('statusText'),
    conn: document.getElementById('conn'),
    mode: document.getElementById('mode'),
    ai: document.getElementById('ai'),
    label: document.getElementById('label'),
    watchers: document.getElementById('watchers'),
    joinOverlay: document.getElementById('joinStreamOverlay'),
    joinBtn: document.getElementById('joinStreamBtn'),
    joinHint: document.getElementById('joinHint'),
    chatDock: document.getElementById('chatDock'),
    chatCollapseBtn: document.getElementById('chatCollapseBtn'),
    chat: document.getElementById('chat'),
    chatInput: document.getElementById('chatInput'),
    sendBtn: document.getElementById('sendBtn'),
    attachBtn: document.getElementById('attachBtn'),
    fileInput: document.getElementById('file'),
    webBtn: document.getElementById('webBtn'),
    searchCloseBtn: document.getElementById('searchCloseBtn'),
    searchPane: document.getElementById('searchPane'),
    searchResults: document.getElementById('searchResults'),
  };
  const v = dom.video;

  const unmuteBtn = document.createElement('button');
  unmuteBtn.textContent = 'Tap for sound';
  unmuteBtn.style.display = 'none';

  let ws = null;
  let pc = null;
  let reconnectDelayMs = 1000;
  let viewerId = `watch-${Math.random().toString(36).slice(2, 10)}`;
  let broadcasterPresent = false;
  let requestPending = false;
  let hasRequestedStream = false;
  let retryTimer = null;
  const DEBUG_CHAT = false;
  let lastChatSendAt = 0;
  let lastChatText = '';

  if (dom.joinOverlay) dom.joinOverlay.appendChild(unmuteBtn);

  function setStatus(text) {
    if (dom.statusText) dom.statusText.textContent = text;
  }

  function showJoinOverlay(show, reason = '') {
    if (!dom.joinOverlay) return;
    dom.joinOverlay.style.display = show ? 'flex' : 'none';
    if (show && dom.joinHint && reason) dom.joinHint.textContent = reason;
  }

  function setStandby(show, reason = 'Waiting for live stream…') {
    if (dom.standby) dom.standby.style.display = show ? 'block' : 'none';
    if (show) setStatus(reason);
  }

  function appendChat(entry) {
    if (!dom.chat) return;
    const wrap = document.createElement('div');
    wrap.className = 'entry';
    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = `${entry.user || entry.sender || 'room'} • ${new Date().toLocaleTimeString()}`;
    const body = document.createElement('div');
    body.textContent = entry.text || '';
    wrap.append(meta, body);
    dom.chat.appendChild(wrap);
    dom.chat.scrollTop = dom.chat.scrollHeight;
  }

  function renderSearchResults(query, result) {
    if (!dom.searchPane || !dom.searchResults) return;
    const results = (((result || {}).data || {}).results || []);
    dom.searchPane.classList.add('open');
    dom.searchResults.textContent = '';
    const frag = document.createDocumentFragment();
    if (!results.length) {
      const empty = document.createElement('div');
      empty.className = 'searchMeta';
      empty.textContent = `No results for "${query}".`;
      frag.appendChild(empty);
    } else {
      for (const item of results.slice(0, 8)) {
        const row = document.createElement('div');
        row.className = 'searchItem';
        const title = document.createElement('a');
        title.href = item.url || '#';
        title.target = '_blank';
        title.rel = 'noopener';
        title.textContent = item.title || item.url || 'Result';
        const snip = document.createElement('div');
        snip.textContent = item.snippet || '';
        const meta = document.createElement('div');
        meta.className = 'searchMeta';
        meta.textContent = item.source || 'web';
        row.append(title, snip, meta);
        frag.appendChild(row);
      }
    }
    dom.searchResults.appendChild(frag);
  }

  function sendJson(type, extra = {}) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type, room, clientId: viewerId, role: 'viewer', ...extra }));
    return true;
  }


  function setLiveMutedAutoplay() {
    if (!v) return;
    v.playsInline = true;
    v.autoplay = true;
    v.muted = true;
  }

  async function tryPlay(reason) {
    if (!dom.video) return;
    try {
      await dom.video.play();
      dom.video.muted = false;
      showJoinOverlay(false);
      console.info('[watch] playback started successfully', { reason, muted: dom.video.muted });
    } catch (err) {
      console.warn('[watch] autoplay blocked', { reason, message: err?.message || String(err) });
      showJoinOverlay(true, 'Tap to join audio');
    }
  }

  function requestStream(force = false) {
    if (force) hasRequestedStream = false;
    scheduleStreamRequest(0);
  }

  function scheduleStreamRequest(delayMs = 350) {
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (!broadcasterPresent || requestPending || hasRequestedStream) return;
      requestPending = sendJson('request_stream');
      hasRequestedStream = requestPending || hasRequestedStream;
      console.info('[watch] request_stream sent', { room, viewerId, requestPending });
    }, delayMs);
  }

  async function iceServers() {
    try {
      const r = await fetch('/webrtc/ice-config');
      const j = await r.json();
      return j.iceServers || [{ urls: 'stun:stun.l.google.com:19302' }];
    } catch {
      return [{ urls: 'stun:stun.l.google.com:19302' }];
    }
  }

  async function ensurePeerConnection(reset = false) {
    if (pc && !reset) return pc;
    if (pc && reset) {
      try { pc.close(); } catch (_) {}
      pc = null;
    }
    pc = new RTCPeerConnection({ iceServers: await iceServers() });
    pc.ontrack = async (event) => {
      const stream = event.streams?.[0] || new MediaStream([event.track]);
      if (dom.video.srcObject !== stream) dom.video.srcObject = stream;
      setStandby(false);
      dom.mode && (dom.mode.textContent = 'LIVE');
      console.info('[watch] remote track attached', { kind: event.track?.kind || 'unknown' });
      await tryPlay('remote_track_attach');
    };
    pc.onicecandidate = (e) => {
      if (!e.candidate) return;
      sendJson('ice-candidate', { viewerId, candidate: e.candidate });
      sendJson('webrtc_ice', { candidate: e.candidate });
    };
    pc.onconnectionstatechange = () => {
      const state = pc?.connectionState || 'unknown';
      console.info('[watch] pc connection state', { state });
      if (state === 'failed' || state === 'disconnected' || state === 'closed') {
        requestPending = false;
        hasRequestedStream = false;
        setStandby(true, 'Reconnecting stream…');
        if (broadcasterPresent) scheduleStreamRequest(500);
      }
    };
    return pc;
  }

  async function onOffer(payload, sourceType) {
    if (!payload?.sdp) return;
    console.info('[watch] offer received', { room, viewerId, sourceType });
    requestPending = false;
    hasRequestedStream = false;
    const localPc = await ensurePeerConnection(true);
    await localPc.setRemoteDescription(payload);
    const answer = await localPc.createAnswer();
    await localPc.setLocalDescription(answer);
    sendJson('answer', { viewerId, sdp: answer.sdp, type: answer.type });
    sendJson('webrtc_answer', { sdp: answer.sdp, type: answer.type });
    console.info('[watch] answer sent', { viewerId });
  }

  async function onServerMessage(msg) {
    if (msg.type === 'connected' && msg.clientId) {
      viewerId = String(msg.clientId);
      console.info('[watch] viewer websocket connected', { room, viewerId });
      return;
    }
    if (msg.type === 'state_sync' || msg.type === 'state_update') {
      const st = msg.state || {};
      broadcasterPresent = !!st.runtime?.broadcaster_present;
      if (dom.watchers) dom.watchers.textContent = `watchers ${st.runtime?.viewer_count ?? 0}`;
      if (dom.ai) dom.ai.textContent = `AI ${st.settings?.ai_status || (st.settings?.ai_enabled ? 'active' : 'idle')}`;
      const present = broadcasterPresent;
      if (present) {
        requestStream();
      }
      if (present && !requestPending) {
        requestStream();
      }
      return;
    }
    if (msg.type === 'presence') {
      broadcasterPresent = !!msg.broadcaster_present;
      if (dom.watchers) dom.watchers.textContent = `watchers ${msg.viewer_count ?? 0}`;
      if (broadcasterPresent) {
        dom.mode && (dom.mode.textContent = 'LIVE');
        scheduleStreamRequest(100);
      } else {
        dom.mode && (dom.mode.textContent = 'OFFLINE');
        setStandby(true, 'Waiting for broadcaster…');
        requestPending = false;
      }
      return;
    }
    if (msg.type === 'stream_started') {
      broadcasterPresent = true;
      requestStream(true);
      return;
    }
    if (msg.type === 'broadcaster-start') {
      broadcasterPresent = true;
      requestStream(true);
      return;
    }
    if (msg.type === 'broadcaster-stop') {
      broadcasterPresent = false;
      requestPending = false;
      hasRequestedStream = false;
      setStandby(true, 'Broadcast ended');
      showJoinOverlay(false);
      return;
    }
    if (msg.type === 'ai_status' && dom.ai) {
      dom.ai.textContent = `AI ${msg.status || 'idle'}`;
      return;
    }
    if (msg.type === 'stage_state') {
      const p = msg.payload || {};
      if (dom.label) dom.label.textContent = p.label || 'PUBLIC ACCESS';
      if (p.mode === 'upload' && p.latestUploadUrl) {
        if (pc) { try { pc.close(); } catch (_) {} pc = null; }
        dom.video.srcObject = null;
        dom.video.src = p.latestUploadUrl;
        dom.video.muted = false;
        await tryPlay('fallback_upload');
        dom.mode && (dom.mode.textContent = 'LATEST UPLOAD');
        setStandby(false);
      }
      return;
    }
    if (msg.type === 'chat' || msg.type === 'ai' || msg.type === 'ai_partial' || msg.type === 'attachment') {
      appendChat(msg);
      return;
    }
    if (msg.type === 'web_search_result') {
      renderSearchResults(msg.query || '', msg.result || {});
      return;
    }
    if (msg.type === 'waiting' || msg.type === 'error') {
      if (msg.message === 'stream_offline' || msg.message === 'no_broadcaster') {
        requestPending = false;
        hasRequestedStream = false;
        setStandby(true, msg.message === 'stream_offline' ? 'Broadcaster connected, waiting for media…' : 'Waiting for broadcaster…');
      }
      return;
    }
    if (msg.type === 'offer' || msg.type === 'watch_offer' || msg.type === 'webrtc_offer') {
      await onOffer(msg.payload, msg.type);
      return;
    }
    if (msg.type === 'ice-candidate' || msg.type === 'webrtc_ice') {
      const candidate = msg.candidate || msg.payload?.candidate;
      if (!candidate) return;
      await ensurePeerConnection();
      try {
        await pc.addIceCandidate(candidate);
        console.info('[watch] ice received', { viewerId, type: msg.type });
      } catch (err) {
        console.warn('[watch] ice add failed', { message: err?.message || String(err) });
      }
    }
  }

  function connect() {
    const url = `${wsBase}/ws/watch`;
    ws = new WebSocket(url);
    dom.conn && (dom.conn.textContent = 'connecting');

    ws.onopen = () => {
      reconnectDelayMs = 1000;
      dom.conn && (dom.conn.textContent = 'connected');
      requestPending = false;
      hasRequestedStream = false;
      setStandby(true, 'Waiting for live stream…');
      setLiveMutedAutoplay();
      sendJson('join');
      requestStream();
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      onServerMessage(msg).catch((err) => console.warn('[watch] message handling failed', err));
    };

    ws.onerror = (err) => console.warn('[watch] websocket error', { room, err });

    ws.onclose = (ev) => {
      dom.conn && (dom.conn.textContent = 'reconnecting');
      requestPending = false;
      hasRequestedStream = false;
      setStandby(true, 'Reconnecting viewer socket…');
      console.warn('[watch] websocket disconnected', { code: ev.code, reason: ev.reason, reconnectDelayMs });
      setTimeout(connect, reconnectDelayMs);
      reconnectDelayMs = Math.min(20000, Math.round(reconnectDelayMs * 1.8));
    };
  }

  dom.joinBtn?.addEventListener('click', async () => {
    dom.video.muted = false;
    await tryPlay('manual_overlay_click');
  });

  dom.chatCollapseBtn?.addEventListener('click', () => {
    if (!dom.chatDock) return;
    dom.chatDock.classList.toggle('collapsed');
    dom.chatCollapseBtn.textContent = dom.chatDock.classList.contains('collapsed') ? 'Expand' : 'Collapse';
  });

  function sendChatMessage() {
    const text = (dom.chatInput?.value || '').trim();
    if (!text) return;
    const now = Date.now();
    if (text === lastChatText && (now - lastChatSendAt) < 400) return;
    lastChatText = text;
    lastChatSendAt = now;
    if (DEBUG_CHAT) console.debug('[watch.chat] send', { chars: text.length });
    sendJson('chat', { text });
    dom.chatInput.value = '';
  }

  dom.sendBtn?.addEventListener('click', sendChatMessage);

  dom.chatInput?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      sendChatMessage();
    }
  });

  dom.attachBtn?.addEventListener('click', () => dom.fileInput?.click());
  dom.fileInput?.addEventListener('change', async () => {
    const f = dom.fileInput.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f, f.name);
    const uploadType = /^image\//i.test(f.type || '') ? 'image' : 'location_video';
    fd.append('upload_type', uploadType);
    try {
      const r = await fetch('/api/upload', { method: 'POST', body: fd });
      const payload = await r.json();
      sendJson('attachment', { attachment: payload });
      sendJson('attachment_uploaded', { attachment: payload });
      appendChat({ user: 'you', text: `Uploaded: ${payload.url || payload.path || f.name}` });
    } catch (err) {
      appendChat({ user: 'system', text: `Upload failed: ${err?.message || String(err)}` });
    } finally {
      dom.fileInput.value = '';
    }
  });

  dom.webBtn?.addEventListener('click', () => {
    const query = (dom.chatInput?.value || '').trim();
    if (!query) return;
    if (DEBUG_CHAT) console.debug('[watch.chat] web_search send', { query_len: query.length });
    sendJson('web_search', { query });
  });

  dom.searchCloseBtn?.addEventListener('click', () => dom.searchPane?.classList.remove('open'));

  connect();
})();
