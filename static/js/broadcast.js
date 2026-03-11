(() => {
  const cfg = window.BROADCAST_CONFIG || {};
  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsBase = `${wsProto}://${location.host}`;

  const dom = {
    preview: document.getElementById('preview'),
    chat: document.getElementById('chat'),
    chatInput: document.getElementById('chatInput'),
    sendBtn: document.getElementById('sendBtn'),
    roomStatus: document.getElementById('roomStatus'),
    stRoom: document.getElementById('stRoom'),
    stServer: document.getElementById('stServer'),
    stRoomConn: document.getElementById('stRoomConn'),
    stLive: document.getElementById('stLive'),
    stAi: document.getElementById('stAi'),
    stWatchers: document.getElementById('stWatchers'),
    stPc: document.getElementById('stPc'),
    stIce: document.getElementById('stIce'),
    ledServer: document.getElementById('ledServer'),
    ledRoom: document.getElementById('ledRoom'),
    ledLive: document.getElementById('ledLive'),
    ledAi: document.getElementById('ledAi'),
    camBtn: document.getElementById('camBtn'),
    screenBtn: document.getElementById('screenBtn'),
    micBtn: document.getElementById('micBtn'),
    sttBtn: document.getElementById('sttBtn'),
    ncBtn: document.getElementById('ncBtn'),
    aiEnableBtn: document.getElementById('aiEnableBtn'),
    aiStatusBtn: document.getElementById('aiStatusBtn'),
    ttsMonBtn: document.getElementById('ttsMonBtn'),
    speakBtn: document.getElementById('speakBtn'),
    attachBtn: document.getElementById('attachBtn'),
    webBtn: document.getElementById('webBtn'),
    searchCloseBtn: document.getElementById('searchCloseBtn'),
    searchPane: document.getElementById('searchPane'),
    searchFrame: document.getElementById('searchFrame'),
    searchFallback: document.getElementById('searchFallback'),
    searchOpenLink: document.getElementById('searchOpenLink'),
    fileInput: document.getElementById('file'),
  };

  let chatRetryMs = 1200;
  let signalRetryMs = 1200;

  const state = {
    room: cfg.room || new URLSearchParams(location.search).get('room') || 'default',
    clientId: `b-${Math.random().toString(36).slice(2, 10)}`,
    pc: null,
    chatWs: null,
    signalWs: null,
    camStream: null,
    screenStream: null,
    mediaRecorder: null,
    media: {
      ai_enabled: true,
      ai_status: 'idle',
      stt_enabled: true,
      tts_enabled: true,
      hear_ai_voice: true,
      mic_enabled: true,
      camera_enabled: true,
      screen_enabled: false,
      noise_cancel_enabled: true,
    },
  };
  dom.stRoom && (dom.stRoom.textContent = state.room);

  function setLed(el, on) {
    if (!el) return;
    el.classList.remove('r', 'g');
    el.classList.add(on ? 'g' : 'r');
  }

  function sendJson(ws, type, extra = {}) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type, room: state.room, clientId: state.clientId, role: 'broadcaster', ...extra }));
  }

  function applyPresence(presence) {
    const viewers = Number(presence.viewer_count ?? 0);
    dom.stWatchers && (dom.stWatchers.textContent = `${viewers}`);
    dom.roomStatus && (dom.roomStatus.textContent = `viewers: ${viewers}`);
    dom.stLive && (dom.stLive.textContent = presence.broadcaster_present ? 'live' : 'offline');
    setLed(dom.ledLive, !!presence.broadcaster_present);
  }

  function updateAiStatus(status) {
    state.media.ai_status = status || 'idle';
    dom.stAi && (dom.stAi.textContent = state.media.ai_status);
    if (dom.aiStatusBtn) dom.aiStatusBtn.textContent = `AI ${state.media.ai_status}`;
    setLed(dom.ledAi, state.media.ai_status === 'active' || state.media.ai_enabled);
  }

  function applyRoomState(next = {}) {
    const settings = next.settings || {};
    state.media = { ...state.media, ...settings };
    if (next.runtime) applyPresence(next.runtime);
    updateAiStatus(state.media.ai_status);

    const setTxt = (id, txt) => { const n = document.getElementById(id); if (n) n.textContent = txt; };
    setLed(document.getElementById('camLed'), !!state.media.camera_enabled);
    setTxt('camTxt', `CAM: ${state.media.camera_enabled ? 'on' : 'off'}`);
    setLed(document.getElementById('screenLed'), !!state.media.screen_enabled);
    setTxt('screenTxt', `SCREEN: ${state.media.screen_enabled ? 'on' : 'off'}`);
    setLed(document.getElementById('micLed'), !!state.media.mic_enabled);
    setTxt('micTxt', `MIC: ${state.media.mic_enabled ? 'on' : 'off'}`);
    setLed(document.getElementById('sttLed'), !!state.media.stt_enabled);
    setTxt('sttTxt', `STT: ${state.media.stt_enabled ? 'on' : 'off'}`);
    setLed(document.getElementById('ncLed'), !!state.media.noise_cancel_enabled);
    setTxt('ncTxt', `NoiseCancel: ${state.media.noise_cancel_enabled ? 'on' : 'off'}`);
    setLed(document.getElementById('ttsMonLed'), !!state.media.hear_ai_voice);
    setTxt('ttsMonTxt', `Hear AI voice: ${state.media.hear_ai_voice ? 'on' : 'off'}`);
    setLed(document.getElementById('aiEnableLed'), !!state.media.ai_enabled);
    setTxt('aiEnableTxt', `AI: ${state.media.ai_enabled ? 'on' : 'off'}`);
  }

  function appendChat(msg) {
    if (!dom.chat) return;
    const d = document.createElement('div');
    d.className = 'entry';
    const who = msg.user || msg.sender || 'system';
    const text = msg.text || msg.payload?.text || '';
    d.innerHTML = `<b>[${who}]</b><div>${String(text).replace(/[<>&]/g, (s)=>({"<":"&lt;",">":"&gt;","&":"&amp;"}[s]))}</div>`;
    if (msg.attachment?.url) {
      const a = document.createElement('a');
      a.href = msg.attachment.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = msg.attachment.name || 'attachment';
      d.appendChild(a);
    }
    dom.chat.appendChild(d);
    dom.chat.scrollTop = dom.chat.scrollHeight;
  }

  function updateConnectivity(online) {
    dom.stServer && (dom.stServer.textContent = online ? 'connected' : 'disconnected');
    dom.stRoomConn && (dom.stRoomConn.textContent = online ? 'connected' : 'disconnected');
    setLed(dom.ledServer, online);
    setLed(dom.ledRoom, online);
  }

  async function startCameraStream() {
    if (state.camStream) return state.camStream;
    state.camStream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: {
        echoCancellation: true,
        noiseSuppression: !!state.media.noise_cancel_enabled,
        autoGainControl: true,
      },
    });
    if (dom.preview && !state.media.screen_enabled) dom.preview.srcObject = state.camStream;
    return state.camStream;
  }

  async function startScreenStream() {
    if (state.screenStream) return state.screenStream;
    state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    state.screenStream.getVideoTracks().forEach((t) => t.addEventListener('ended', () => {
      if (!state.media.screen_enabled) return;
      state.media.screen_enabled = false;
      switchToCamera().catch(() => announceState());
    }));
    if (dom.preview) dom.preview.srcObject = state.screenStream;
    return state.screenStream;
  }

  async function ensurePeerConnection() {
    if (state.pc) return state.pc;
    const iceCfg = await fetch(cfg.iceConfigUrl || '/webrtc/ice-config').then((r) => r.json()).catch(() => ({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] }));
    const pc = new RTCPeerConnection({ iceServers: iceCfg.iceServers || [{ urls: 'stun:stun.l.google.com:19302' }] });
    state.pc = pc;
    pc.onicecandidate = (e) => { if (e.candidate) sendJson(state.signalWs, 'webrtc_ice', { candidate: e.candidate }); };
    pc.onconnectionstatechange = () => dom.stPc && (dom.stPc.textContent = pc.connectionState);
    pc.oniceconnectionstatechange = () => dom.stIce && (dom.stIce.textContent = pc.iceConnectionState);
    return pc;
  }

  async function replaceOutgoingVideoTrack(newTrack) {
    const pc = await ensurePeerConnection();
    const sender = pc.getSenders().find((s) => s.track && s.track.kind === 'video');
    if (sender) {
      await sender.replaceTrack(newTrack || null);
    } else if (newTrack) {
      pc.addTrack(newTrack, state.media.screen_enabled ? state.screenStream : state.camStream);
      await negotiate('add-video');
    }
  }

  async function replaceOutgoingAudioTrack(newTrack) {
    const pc = await ensurePeerConnection();
    const sender = pc.getSenders().find((s) => s.track && s.track.kind === 'audio');
    if (sender) {
      await sender.replaceTrack(newTrack || null);
    } else if (newTrack) {
      pc.addTrack(newTrack, state.camStream || new MediaStream([newTrack]));
      await negotiate('add-audio');
    }
  }

  async function syncTracks() {
    if (state.media.screen_enabled) {
      const screen = await startScreenStream();
      await replaceOutgoingVideoTrack(screen.getVideoTracks()[0] || null);
    } else {
      const cam = await startCameraStream();
      await replaceOutgoingVideoTrack(state.media.camera_enabled ? (cam.getVideoTracks()[0] || null) : null);
    }
    if (state.media.mic_enabled) {
      const cam = await startCameraStream();
      await replaceOutgoingAudioTrack(cam.getAudioTracks()[0] || null);
    } else {
      await replaceOutgoingAudioTrack(null);
    }
  }

  async function negotiate(reason = 'update') {
    const pc = await ensurePeerConnection();
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    sendJson(state.signalWs, 'webrtc_offer', { sdp: offer.sdp, type: offer.type, reason });
  }

  async function switchToScreen() {
    state.media.screen_enabled = true;
    await syncTracks();
    announceState();
  }

  async function switchToCamera() {
    state.media.screen_enabled = false;
    if (state.screenStream) {
      state.screenStream.getTracks().forEach((t) => t.stop());
      state.screenStream = null;
    }
    await syncTracks();
    if (dom.preview && state.camStream) dom.preview.srcObject = state.camStream;
    announceState();
  }

  function stopSpeechCapture() {
    if (state.mediaRecorder) {
      try { state.mediaRecorder.stop(); } catch {}
      state.mediaRecorder = null;
    }
  }

  function sendAudioChunk(b64, mime, sampleRate, channels) {
    sendJson(state.chatWs, 'audio_chunk', { mime, data: b64, sampleRate, channels });
  }

  async function startSpeechCaptureFromMic() {
    stopSpeechCapture();
    if (!state.media.stt_enabled || !state.media.mic_enabled) return;
    const cam = await startCameraStream();
    const track = cam.getAudioTracks()[0];
    if (!track) return;
    const sttStream = new MediaStream([track.clone()]);
    const preferredMime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((m) => {
      try { return MediaRecorder.isTypeSupported(m); } catch (_) { return false; }
    }) || '';
    const mr = preferredMime ? new MediaRecorder(sttStream, { mimeType: preferredMime }) : new MediaRecorder(sttStream);
    const settings = track.getSettings ? track.getSettings() : {};
    const sampleRate = Number(settings.sampleRate || 0) || 0;
    const channels = Number(settings.channelCount || 1) || 1;
    mr.ondataavailable = async (ev) => {
      if (!ev.data || ev.data.size < 1) return;
      const ab = await ev.data.arrayBuffer();
      const bytes = new Uint8Array(ab);
      let binary = '';
      const chunkSize = 0x8000;
      for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
      }
      sendAudioChunk(btoa(binary), ev.data.type || preferredMime || 'audio/webm;codecs=opus', sampleRate, channels);
    };
    mr.start(1200);
    state.mediaRecorder = mr;
  }

  function connectChat() {
    const ws = new WebSocket(`${wsBase}/ws/chat`);
    state.chatWs = ws;
    ws.onopen = () => {
      chatRetryMs = 1200;
      updateConnectivity(true);
      sendJson(ws, 'join', { role: 'participant' });
      startSpeechCaptureFromMic().catch(() => {});
    };
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === 'state_sync' || msg.type === 'state_update') applyRoomState(msg.state || {});
      if (msg.type === 'presence') applyPresence(msg);
      if (msg.type === 'ai_status') updateAiStatus(msg.status || 'idle');
      if (['chat', 'ai', 'ai_partial', 'attachment'].includes(msg.type)) appendChat(msg);
      if (msg.type === 'web_search_result') {
        const q = encodeURIComponent(msg.query || '');
        const fallbackUrl = `https://www.google.com/search?q=${q}`;
        if (dom.searchOpenLink) dom.searchOpenLink.href = fallbackUrl;
        if (dom.searchFrame) dom.searchFrame.src = fallbackUrl;
        if (dom.searchPane) dom.searchPane.classList.add('open');
        if (dom.searchFallback) dom.searchFallback.classList.add('show');
      }
    };
    ws.onclose = () => {
      updateConnectivity(false);
      stopSpeechCapture();
      setTimeout(connectChat, chatRetryMs);
      chatRetryMs = Math.min(15000, Math.round(chatRetryMs * 1.7));
    };
  }

  function connectSignal() {
    const ws = new WebSocket(`${wsBase}/ws/broadcast`);
    state.signalWs = ws;
    ws.onopen = async () => {
      signalRetryMs = 1200;
      sendJson(ws, 'join', { role: 'broadcaster' });
      await syncTracks();
      await negotiate('initial');
    };
    ws.onmessage = async (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === 'webrtc_answer' && msg.sdp && state.pc) {
        await state.pc.setRemoteDescription({ type: msg.answerType || 'answer', sdp: msg.sdp });
      }
      if (msg.type === 'presence') applyPresence(msg);
      if (msg.type === 'state_sync' || msg.type === 'state_update') applyRoomState(msg.state || {});
    };
    ws.onclose = () => {
      setTimeout(connectSignal, signalRetryMs);
      signalRetryMs = Math.min(15000, Math.round(signalRetryMs * 1.7));
    };
  }

  function announceState() {
    sendJson(state.chatWs, 'toggle_state', { state: state.media });
    sendJson(state.signalWs, 'toggle_state', { state: state.media });
    sendJson(state.signalWs, 'set_media_mode', { camera: state.media.camera_enabled, screen: state.media.screen_enabled, mic: state.media.mic_enabled });
    applyRoomState({ settings: state.media, runtime: { broadcaster_present: true, viewer_count: Number(dom.stWatchers?.textContent || 0) } });
  }

  dom.sendBtn?.addEventListener('click', () => {
    const text = dom.chatInput?.value?.trim();
    if (!text) return;
    sendJson(state.chatWs, 'chat', { text });
    dom.chatInput.value = '';
  });
  dom.chatInput?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); dom.sendBtn?.click(); }
  });

  dom.webBtn?.addEventListener('click', () => {
    const query = (dom.chatInput?.value || '').trim();
    if (!query) return;
    sendJson(state.chatWs, 'web_search', { query });
  });
  dom.searchCloseBtn?.addEventListener('click', () => dom.searchPane?.classList.remove('open'));

  dom.attachBtn?.addEventListener('click', () => dom.fileInput?.click());
  dom.fileInput?.addEventListener('change', async () => {
    const f = dom.fileInput.files?.[0];
    if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    const res = await fetch('/api/upload', { method: 'POST', body: fd }).then((r) => r.json()).catch(() => ({}));
    sendJson(state.chatWs, 'attachment_uploaded', { attachment: { url: res.url || '', name: f.name, mime: f.type, size: f.size } });
    dom.fileInput.value = '';
  });

  dom.camBtn?.addEventListener('click', async () => {
    state.media.camera_enabled = !state.media.camera_enabled;
    await syncTracks();
    announceState();
  });
  dom.screenBtn?.addEventListener('click', async () => {
    if (state.media.screen_enabled) await switchToCamera(); else await switchToScreen();
  });
  dom.micBtn?.addEventListener('click', async () => {
    state.media.mic_enabled = !state.media.mic_enabled;
    await syncTracks();
    if (state.media.mic_enabled && state.media.stt_enabled) await startSpeechCaptureFromMic();
    else stopSpeechCapture();
    announceState();
  });
  dom.sttBtn?.addEventListener('click', async () => {
    state.media.stt_enabled = !state.media.stt_enabled;
    if (state.media.stt_enabled && state.media.mic_enabled) await startSpeechCaptureFromMic();
    else stopSpeechCapture();
    announceState();
  });
  dom.ncBtn?.addEventListener('click', async () => {
    state.media.noise_cancel_enabled = !state.media.noise_cancel_enabled;
    if (state.camStream) { state.camStream.getTracks().forEach((t) => t.stop()); state.camStream = null; }
    await syncTracks();
    if (state.media.stt_enabled && state.media.mic_enabled) await startSpeechCaptureFromMic();
    announceState();
  });
  dom.aiEnableBtn?.addEventListener('click', () => { state.media.ai_enabled = !state.media.ai_enabled; announceState(); });
  dom.ttsMonBtn?.addEventListener('click', () => { state.media.hear_ai_voice = !state.media.hear_ai_voice; announceState(); });
  dom.speakBtn?.addEventListener('click', () => {
    const lastAi = [...(dom.chat?.querySelectorAll('.entry') || [])].reverse().find((e) => (e.textContent || '').toLowerCase().includes('[ai]'));
    if (!lastAi) return;
    const text = (lastAi.textContent || '').replace(/\[ai\]/ig, '').trim();
    if (text) sendJson(state.chatWs, 'chat', { text });
  });

  applyRoomState({ settings: state.media, runtime: { broadcaster_present: false, viewer_count: 0 } });
  connectChat();
  connectSignal();

  // Best effort startup: UI stays ON even if browser prompts for permissions first.
  startCameraStream().then(syncTracks).then(() => { announceState(); startSpeechCaptureFromMic().catch(() => {}); }).catch(() => announceState());
})();
