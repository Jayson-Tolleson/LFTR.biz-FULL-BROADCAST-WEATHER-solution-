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
    recordBtn: document.getElementById('recordBtn'),
    rtmpBtn: document.getElementById('rtmpBtn'),
    rtmpKeyInput: document.getElementById('rtmpKeyInput'),
    recordingStatus: document.getElementById('recordingStatus'),
    attachBtn: document.getElementById('attachBtn'),
    webBtn: document.getElementById('webBtn'),
    searchCloseBtn: document.getElementById('searchCloseBtn'),
    searchPane: document.getElementById('searchPane'),
    searchResults: document.getElementById('searchResults'),
    fileInput: document.getElementById('file'),
    chatCollapseBtn: document.getElementById('chatCollapseBtn'),
    chatPanel: document.querySelector('.chat'),
  };

  let chatRetryMs = 1200;
  let signalRetryMs = 1200;
  const DEBUG_CHAT = false;
  let lastChatSendAt = 0;
  let lastChatText = '';
  let selectedVideoDeviceId = '';
  let cachedVideoInputs = [];
  let cameraCycleIndex = -1;
  let recordingStream = null;
  let recordingChunks = [];
  let recordingMedia = null;

  const state = {
    room: cfg.room || new URLSearchParams(location.search).get('room') || 'default',
    clientId: `b-${Math.random().toString(36).slice(2, 10)}`,
    pc: null,
    peerConnections: {},
    chatWs: null,
    signalWs: null,
    camStream: null,
    screenStream: null,
    speechCtx: null,
    speechSource: null,
    speechProcessor: null,
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
      record_enabled: false,
      rtmp_enabled: false,
      rtmp_url: '',
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
    if (!state.media.camera_enabled) {
      setTxt('camTxt', 'CAM: off');
    }
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
    setLed(document.getElementById('recordLed'), !!state.media.record_enabled);
    setTxt('recordTxt', `Record: ${state.media.record_enabled ? 'on' : 'off'}`);
    setLed(document.getElementById('rtmpLed'), !!state.media.rtmp_enabled);
    setTxt('rtmpTxt', `RTMP: ${state.media.rtmp_enabled ? 'on' : 'off'}`);
  }

  function compactCameraName(label) {
    const text = String(label || '').trim();
    if (!text) return 'camera';
    if (/front|user/i.test(text)) return 'front cam';
    if (/back|rear|environment/i.test(text)) return 'back cam';
    return text.length > 18 ? `${text.slice(0, 18)}…` : text;
  }

  function updateCameraLabel(label) {
    const camTxt = document.getElementById('camTxt');
    if (!camTxt) return;
    camTxt.textContent = state.media.camera_enabled ? `CAM: ${compactCameraName(label)}` : 'CAM: off';
  }

  function appendChat(msg) {
    if (!dom.chat) return;
    const startedAt = performance.now();
    const d = document.createElement('article');
    d.className = 'entry';
    const who = msg.user || msg.sender || 'system';
    const text = msg.text || msg.payload?.text || '';
    const whoEl = document.createElement('b');
    whoEl.textContent = `[${who}]`;
    const textEl = document.createElement('div');
    textEl.textContent = String(text);
    d.append(whoEl, textEl);
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
    if (DEBUG_CHAT) {
      console.debug('[broadcast.chat] render_ms', Math.round((performance.now() - startedAt) * 1000) / 1000);
    }
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

  function updateConnectivity(online) {
    dom.stServer && (dom.stServer.textContent = online ? 'connected' : 'disconnected');
    dom.stRoomConn && (dom.stRoomConn.textContent = online ? 'connected' : 'disconnected');
    setLed(dom.ledServer, online);
    setLed(dom.ledRoom, online);
  }

  async function startCameraStream() {
    if (state.camStream) {
      const activeTrack = state.camStream.getVideoTracks()[0];
      const activeDeviceId = activeTrack?.getSettings?.().deviceId || '';
      if (!selectedVideoDeviceId || selectedVideoDeviceId === activeDeviceId) {
        return state.camStream;
      }
      state.camStream.getTracks().forEach((t) => t.stop());
      state.camStream = null;
    }
    const videoConstraints = selectedVideoDeviceId ? { deviceId: { exact: selectedVideoDeviceId } } : { facingMode: 'user' };
    state.camStream = await navigator.mediaDevices.getUserMedia({
      video: videoConstraints,
      audio: {
        echoCancellation: true,
        noiseSuppression: !!state.media.noise_cancel_enabled,
        autoGainControl: true,
        channelCount: 1,
        sampleRate: 48000,
      },
    });
    if (dom.preview && !state.media.screen_enabled) dom.preview.srcObject = state.camStream;
    const track = state.camStream.getVideoTracks()[0];
    selectedVideoDeviceId = track?.getSettings?.().deviceId || selectedVideoDeviceId;
    updateCameraLabel(track?.label || 'camera');
    return state.camStream;
  }

  async function refreshVideoInputs() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      cachedVideoInputs = devices.filter((d) => d.kind === 'videoinput');
    } catch {
      cachedVideoInputs = [];
    }
    return cachedVideoInputs;
  }

  async function cycleCameraDevice() {
    const inputs = await refreshVideoInputs();
    if (!inputs.length) {
      state.media.camera_enabled = !state.media.camera_enabled;
      await syncTracks();
      announceState();
      return;
    }
    const idx = inputs.findIndex((d) => d.deviceId === selectedVideoDeviceId);
    cameraCycleIndex = idx >= 0 ? idx : cameraCycleIndex;
    cameraCycleIndex = (cameraCycleIndex + 1) % inputs.length;
    selectedVideoDeviceId = inputs[cameraCycleIndex].deviceId;
    state.media.camera_enabled = true;
    if (state.camStream) {
      state.camStream.getTracks().forEach((t) => t.stop());
      state.camStream = null;
    }
    await syncTracks();
    updateCameraLabel(inputs[cameraCycleIndex].label || `camera ${cameraCycleIndex + 1}`);
    announceState();
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

  async function ensurePeerConnection(viewerId = null) {
    if (!viewerId && state.pc) return state.pc;
    if (viewerId && state.peerConnections[viewerId]) return state.peerConnections[viewerId];
    const iceCfg = await fetch(cfg.iceConfigUrl || '/webrtc/ice-config').then((r) => r.json()).catch(() => ({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] }));
    const pc = new RTCPeerConnection({ iceServers: iceCfg.iceServers || [{ urls: 'stun:stun.l.google.com:19302' }] });
    if (viewerId) state.peerConnections[viewerId] = pc;
    else state.pc = pc;
    pc.onicecandidate = (e) => {
      if (!e.candidate) return;
      if (viewerId) sendJson(state.signalWs, 'ice-candidate', { viewerId, candidate: e.candidate });
      else sendJson(state.signalWs, 'webrtc_ice', { candidate: e.candidate });
    };
    pc.onconnectionstatechange = () => dom.stPc && (dom.stPc.textContent = pc.connectionState);
    pc.oniceconnectionstatechange = () => dom.stIce && (dom.stIce.textContent = pc.iceConnectionState);
    if (viewerId) {
      const stream = state.media.screen_enabled ? state.screenStream : state.camStream;
      (stream?.getTracks?.() || []).forEach((track) => pc.addTrack(track, stream));
    }
    return pc;
  }

  async function createOfferForViewer(viewerId) {
    const pc = await ensurePeerConnection(viewerId);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    sendJson(state.signalWs, 'offer', { viewerId, sdp: offer.sdp, type: offer.type });
  }

  function removeViewerPeer(viewerId) {
    const pc = state.peerConnections[viewerId];
    if (!pc) return;
    try { pc.close(); } catch (_) {}
    delete state.peerConnections[viewerId];
  }

  async function replaceOutgoingVideoTrack(newTrack) {
    for (const pc of Object.values(state.peerConnections)) {
      const sender = pc.getSenders().find((s) => s.track && s.track.kind === 'video');
      if (sender) await sender.replaceTrack(newTrack || null);
    }
  }

  async function replaceOutgoingAudioTrack(newTrack) {
    for (const pc of Object.values(state.peerConnections)) {
      const sender = pc.getSenders().find((s) => s.track && s.track.kind === 'audio');
      if (sender) await sender.replaceTrack(newTrack || null);
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

  const STT_TARGET_SAMPLE_RATE = 16000;
  const STT_TARGET_CHANNELS = 1;

  function stopSpeechCapture() {
    if (state.speechProcessor) {
      try { state.speechProcessor.disconnect(); } catch (_) {}
      state.speechProcessor.onaudioprocess = null;
      state.speechProcessor = null;
    }
    if (state.speechSource) {
      try { state.speechSource.disconnect(); } catch (_) {}
      state.speechSource = null;
    }
    if (state.speechCtx) {
      try { state.speechCtx.close(); } catch (_) {}
      state.speechCtx = null;
    }
  }

  async function playAiVoice(url) {
    if (!url || !state.media.hear_ai_voice) return;
    try {
      const audio = new Audio(url);
      audio.volume = 0.9;
      await audio.play();
    } catch (_) {}
  }

  function currentProgramStream() {
    const base = state.media.screen_enabled ? state.screenStream : state.camStream;
    if (!base) return null;
    const tracks = [];
    const videoTrack = base.getVideoTracks()[0];
    const audioTrack = base.getAudioTracks()[0];
    if (videoTrack) tracks.push(videoTrack.clone());
    if (audioTrack) tracks.push(audioTrack.clone());
    return tracks.length ? new MediaStream(tracks) : null;
  }

  async function uploadRecording(blob) {
    const fd = new FormData();
    fd.append('file', new File([blob], `broadcast-${Date.now()}.webm`, { type: blob.type || 'video/webm' }));
    const resp = await fetch(`/api/broadcast/recording?room=${encodeURIComponent(state.room)}`, { method: 'POST', body: fd });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || !payload?.ok) throw new Error(payload?.error || `record upload failed (${resp.status})`);
    return payload;
  }

  async function toggleRecording() {
    if (recordingMedia && recordingMedia.state === 'recording') {
      recordingMedia.stop();
      state.media.record_enabled = false;
      announceState();
      return;
    }
    const stream = currentProgramStream();
    if (!stream) {
      if (dom.recordingStatus) dom.recordingStatus.textContent = 'Record unavailable: no active camera/screen';
      return;
    }
    recordingChunks = [];
    recordingStream = stream;
    recordingMedia = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8,opus' });
    recordingMedia.ondataavailable = (ev) => { if (ev.data?.size) recordingChunks.push(ev.data); };
    recordingMedia.onstop = async () => {
      const blob = new Blob(recordingChunks, { type: 'video/webm' });
      recordingChunks = [];
      recordingStream?.getTracks().forEach((t) => t.stop());
      recordingStream = null;
      if (dom.recordingStatus) dom.recordingStatus.textContent = 'Processing MP4…';
      try {
        const saved = await uploadRecording(blob);
        if (dom.recordingStatus) dom.recordingStatus.textContent = `Saved ${saved.url || 'recording'}`;
      } catch (err) {
        if (dom.recordingStatus) dom.recordingStatus.textContent = `Recording failed: ${err.message || err}`;
      }
    };
    recordingMedia.start(1000);
    state.media.record_enabled = true;
    if (dom.recordingStatus) dom.recordingStatus.textContent = 'Recording live…';
    announceState();
  }

  async function toggleRtmp() {
    const next = !state.media.rtmp_enabled;
    const streamKey = (dom.rtmpKeyInput?.value || '').trim();
    const res = await fetch('/api/broadcast/rtmp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room: state.room, enabled: next, stream_key: streamKey }),
    }).then((r) => r.json()).catch(() => ({ ok: false }));
    if (!res.ok) return;
    state.media.rtmp_enabled = !!res.enabled;
    state.media.rtmp_url = res.rtmp_url || '';
    announceState();
  }

  function sendAudioChunk(b64, sampleRate) {
    sendJson(state.chatWs, 'audio_chunk', {
      encoding: 'linear16',
      channels: STT_TARGET_CHANNELS,
      sampleRate,
      data: b64,
    });
  }

  function floatToInt16(input) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i += 1) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
    }
    return out;
  }

  function downsampleBuffer(input, inputRate, outputRate) {
    if (outputRate === inputRate) return input;
    const ratio = inputRate / outputRate;
    const outLength = Math.max(1, Math.round(input.length / ratio));
    const out = new Float32Array(outLength);
    let outOffset = 0;
    let inOffset = 0;
    while (outOffset < outLength) {
      const nextOffset = Math.min(input.length, Math.round((outOffset + 1) * ratio));
      let acc = 0;
      let count = 0;
      for (let i = inOffset; i < nextOffset; i += 1) {
        acc += input[i];
        count += 1;
      }
      out[outOffset] = count > 0 ? acc / count : 0;
      outOffset += 1;
      inOffset = nextOffset;
    }
    return out;
  }

  function int16ToBase64(samples) {
    const bytes = new Uint8Array(samples.buffer);
    let binary = '';
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
  }

  async function startSpeechCaptureFromMic() {
    stopSpeechCapture();
    if (!state.media.stt_enabled || !state.media.mic_enabled) return;
    const cam = await startCameraStream();
    const track = cam.getAudioTracks()[0];
    if (!track) return;

    const sttStream = new MediaStream([track.clone()]);
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;

    const ctx = new Ctx({ sampleRate: STT_TARGET_SAMPLE_RATE });
    const source = ctx.createMediaStreamSource(sttStream);
    const processor = ctx.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (event) => {
      if (!state.chatWs || state.chatWs.readyState !== WebSocket.OPEN) return;
      const input = event.inputBuffer.getChannelData(0);
      const reduced = downsampleBuffer(input, ctx.sampleRate, STT_TARGET_SAMPLE_RATE);
      const pcm = floatToInt16(reduced);
      if (!pcm.length) return;
      sendAudioChunk(int16ToBase64(pcm), STT_TARGET_SAMPLE_RATE);
    };

    source.connect(processor);
    processor.connect(ctx.destination);

    state.speechCtx = ctx;
    state.speechSource = source;
    state.speechProcessor = processor;
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
      if (msg.type === 'ai' && msg.voice) playAiVoice(msg.voice);
      if (msg.type === 'web_search_result') {
        renderSearchResults(msg.query || '', msg.result || {});
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
      sendJson(ws, 'media_ready');
    };
    ws.onmessage = async (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === 'viewer_joined' && msg.viewerId) {
        await createOfferForViewer(msg.viewerId);
      }
      if (msg.type === 'viewer_left' && msg.viewerId) {
        removeViewerPeer(msg.viewerId);
      }
      if (msg.type === 'answer' && msg.viewerId && msg.payload?.sdp) {
        const pc = state.peerConnections[msg.viewerId];
        if (pc) await pc.setRemoteDescription({ type: msg.payload.type || 'answer', sdp: msg.payload.sdp });
      }
      if (msg.type === 'ice-candidate' && msg.viewerId && msg.candidate) {
        const pc = state.peerConnections[msg.viewerId];
        if (pc) {
          try { await pc.addIceCandidate(msg.candidate); } catch (_) {}
        }
      }
      if (msg.type === 'webrtc_answer' && msg.sdp && state.pc) {
        await state.pc.setRemoteDescription({ type: msg.answerType || 'answer', sdp: msg.sdp });
      }
      if (msg.type === 'presence') applyPresence(msg);
      if (msg.type === 'state_sync' || msg.type === 'state_update') applyRoomState(msg.state || {});
    };
    ws.onclose = () => {
      Object.keys(state.peerConnections).forEach(removeViewerPeer);
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

  function sendChatMessage() {
    const text = dom.chatInput?.value?.trim();
    if (!text) return;
    const now = Date.now();
    if (text === lastChatText && (now - lastChatSendAt) < 400) return;
    lastChatText = text;
    lastChatSendAt = now;
    if (DEBUG_CHAT) console.debug('[broadcast.chat] send', { chars: text.length });
    sendJson(state.chatWs, 'chat', { text });
    dom.chatInput.value = '';
  }

  dom.sendBtn?.addEventListener('click', sendChatMessage);
  dom.chatInput?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); sendChatMessage(); }
  });

  dom.webBtn?.addEventListener('click', () => {
    const query = (dom.chatInput?.value || '').trim();
    if (!query) return;
    if (DEBUG_CHAT) console.debug('[broadcast.chat] web_search send', { query_len: query.length });
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
    await cycleCameraDevice();
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
  dom.recordBtn?.addEventListener('click', () => { toggleRecording().catch(() => {}); });
  dom.rtmpBtn?.addEventListener('click', () => { toggleRtmp().catch(() => {}); });
  dom.chatCollapseBtn?.addEventListener('click', () => {
    if (!dom.chatPanel) return;
    dom.chatPanel.classList.toggle('collapsed');
    dom.chatCollapseBtn.textContent = dom.chatPanel.classList.contains('collapsed') ? 'Expand' : 'Collapse';
  });

  if (window.matchMedia && window.matchMedia('(max-width: 980px)').matches && dom.chatPanel) {
    dom.chatPanel.classList.add('collapsed');
    if (dom.chatCollapseBtn) dom.chatCollapseBtn.textContent = 'Expand';
  }
  applyRoomState({ settings: state.media, runtime: { broadcaster_present: false, viewer_count: 0 } });
  connectChat();
  connectSignal();

  // Best effort startup: UI stays ON even if browser prompts for permissions first.
  startCameraStream().then(refreshVideoInputs).then(syncTracks).then(() => { announceState(); startSpeechCaptureFromMic().catch(() => {}); }).catch(() => announceState());
})();
