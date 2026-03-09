(() => {
  const DEFAULT_CONFIG = {
    socketPath: '/socket.io',
    iceConfigUrl: '/webrtc/ice-config',
    aiChatUrl: '/ai/chat',
    aiTtsUrl: '/ai/tts',
    aiWebSearchUrl: '/ai/websearch',
    role: 'broadcast',
    debug: true,
  };

  function safeId(id) { return document.getElementById(id); }
  function b64FromArrayBuffer(ab) { const bytes = new Uint8Array(ab); let bin = ''; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(bin); }

  const App = {
    config: { ...DEFAULT_CONFIG },
    state: {
      room: 'default', socket: null, pc: null,
      txVideo: null, txAudio: null,
      micStream: null, camStream: null, screenStream: null, composedStream: null, localStream: null,
      sttRec: null, sttEnabled: false, sstEnabled: false,
      aiMode: 'active', ttsMonitor: false, noiseCancel: false,
      makingOffer: false, awaitingAnswer: false, needsNegotiation: false, sendLock: false,
      camDevices: [], camIndex: -1, statsInterval: null, lastBytes: 0, lastTs: 0,
      seen: new Set(), lastAiText: '',
    },
    dom: {},

    dbg(...args) { if (this.config.debug) console.log('[broadcast]', ...args); },

    init() {
      this.config = { ...DEFAULT_CONFIG, ...(window.BROADCAST_CONFIG || {}) };
      this.state.room = (new URLSearchParams(location.search).get('room') || 'default').trim() || 'default';
      this.cacheDom();
      this.bindEvents();
      this.connectSocket();
      this.dom.stRoom.textContent = this.state.room;
      this.setAiMode('active');
      this.setSst(false);
    },

    cacheDom() {
      this.dom = {
        preview: safeId('preview'), chat: safeId('chat'), chatInput: safeId('chatInput'),
        roomStatus: safeId('roomStatus'), stRoom: safeId('stRoom'), stSock: safeId('stSock'), stPc: safeId('stPc'), stIce: safeId('stIce'), stStt: safeId('stStt'), stAi: safeId('stAi'), stBr: safeId('stBr'),
        ledSock: safeId('ledSock'), ledRtc: safeId('ledRtc'), ledIce: safeId('ledIce'), ledStt: safeId('ledStt'), ledAi: safeId('ledAi'),
        camBtn: safeId('camBtn'), camLed: safeId('camLed'), camTxt: safeId('camTxt'),
        screenBtn: safeId('screenBtn'), screenLed: safeId('screenLed'), screenTxt: safeId('screenTxt'),
        ncBtn: safeId('ncBtn'), ncLed: safeId('ncLed'), ncTxt: safeId('ncTxt'),
        sttBtn: safeId('sttBtn'), sttLed: safeId('sttLed'), sttTxt: safeId('sttTxt'),
        sstBtn: safeId('sstBtn'), sstLed: safeId('sstLed'), sstTxt: safeId('sstTxt'),
        aiIdleBtn: safeId('aiIdleBtn'), aiActiveBtn: safeId('aiActiveBtn'),
        ttsMonBtn: safeId('ttsMonBtn'), ttsMonLed: safeId('ttsMonLed'), ttsMonTxt: safeId('ttsMonTxt'),
        sendBtn: safeId('sendBtn'), attachBtn: safeId('attachBtn'), webBtn: safeId('webBtn'), searchCloseBtn: safeId('searchCloseBtn'), speakBtn: safeId('speakBtn'), file: safeId('file'),
        searchPane: safeId('searchPane'), searchFrame: safeId('searchFrame'), searchFallback: safeId('searchFallback'), searchOpenLink: safeId('searchOpenLink'),
      };
    },

    bindEvents() {
      this.dom.sendBtn?.addEventListener('click', () => this.sendChat());
      this.dom.chatInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendChat(); }
      });

      this.dom.camBtn?.addEventListener('click', async () => {
        try { await this.cycleCamera(); await this.syncTracks(); } catch { this.renderSystemMessage('[camera] permission denied or unavailable.'); }
      });
      this.dom.screenBtn?.addEventListener('click', async () => {
        try {
          if (this.state.screenStream) { await this.stopScreen(); await this.syncTracks(); }
          else { await this.startScreenWithPiP(); await this.syncTracks(); }
        } catch (e) {
          this.dbg('screen toggle failed', e); await this.stopScreen(); await this.syncTracks();
        }
      });
      this.dom.ncBtn?.addEventListener('click', () => this.setNoiseCancel(!this.state.noiseCancel));
      this.dom.sttBtn?.addEventListener('click', () => (this.state.sttEnabled ? this.stopStt() : this.startStt()));
      this.dom.sstBtn?.addEventListener('click', () => this.setSst(!this.state.sstEnabled));
      this.dom.aiIdleBtn?.addEventListener('click', () => this.setAiMode('idle'));
      this.dom.aiActiveBtn?.addEventListener('click', () => this.setAiMode('active'));
      this.dom.ttsMonBtn?.addEventListener('click', () => this.setTtsMonitor(!this.state.ttsMonitor));
      this.dom.speakBtn?.addEventListener('click', () => this.speakLastAi());
      this.dom.attachBtn?.addEventListener('click', () => this.dom.file?.click());
      this.dom.webBtn?.addEventListener('click', () => this.runWebSearch());
      this.dom.searchCloseBtn?.addEventListener('click', () => this.hideSearchPane());

      this.dom.file?.addEventListener('change', async (e) => {
        const f = e.target.files && e.target.files[0];
        if (!f || !this.state.socket) return;
        const ab = await f.arrayBuffer();
        this.state.socket.emit('upload_file', { name: f.name, mime: f.type, content_base64: b64FromArrayBuffer(ab), text: `uploaded ${f.name}`, upload_type: 'location_video', locationId: this.state.room });
      });
    },

    connectSocket() {
      const socket = io(window.location.origin, { path: this.config.socketPath, transports: ['polling', 'websocket'], query: { room: this.state.room, role: this.config.role } });
      this.state.socket = socket;
      socket.on('connect', async () => {
        this.setLed(this.dom.ledSock, true, true); this.dom.stSock.textContent = 'connected';
        await this.ensureMic().catch(() => {});
        await this.refreshCameras();
        if (this.state.camDevices.length > 0) await this.startCameraByIndex(0).catch(() => {});
        await this.createPeerConnection();
        await this.syncTracks(true);
      });
      socket.on('disconnect', () => { this.setLed(this.dom.ledSock, false); this.dom.stSock.textContent = 'disconnected'; });
      socket.on('webrtc_answer', async (ans) => {
        try {
          await this.createPeerConnection();
          if (this.state.pc && ans?.sdp) {
            await this.state.pc.setRemoteDescription(ans);
            this.state.awaitingAnswer = false;
            if (this.state.needsNegotiation) { this.state.needsNegotiation = false; await this.negotiate('queued'); }
          }
        } catch (e) {
          this.state.awaitingAnswer = false;
          this.dbg('answer failed', e);
        }
      });
      socket.on('webrtc_ice_server', async (p) => {
        try { if (this.state.pc && p?.candidate) await this.state.pc.addIceCandidate(p.candidate || p); } catch (e) { this.dbg('add remote ice failed', e); }
      });
      socket.on('chat_message', (msg) => this.renderChatMessage(msg));
      socket.on('web_search_result', (p) => this.renderWebSearchResult(p));
      socket.on('stt_text', (p) => this.renderChatMessage({ sender: 'stt', role: 'broadcaster_stt', text: p.text, ts: p.ts || Date.now() }));
      socket.on('stt_status', (p) => { if (p?.enabled === false) this.stopStt(); });
      socket.on('room_status', (p) => {
        if (!p) return;
        this.dom.roomStatus.textContent = `viewers:${p.viewer_count || 0} broadcaster:${p.broadcaster_present ? 'yes' : 'no'}`;
        const mode = p.ai?.mode === 'idle' ? 'idle' : 'active';
        this.setAiMode(mode, false);
      });
      socket.on('ai_tts_audio', (p) => { if (p?.url && this.state.ttsMonitor) this.playUrl(p.url); });
    },

    async ensureMic() {
      if (this.state.micStream) return this.state.micStream;
      this.state.micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: this.state.noiseCancel, noiseSuppression: this.state.noiseCancel, autoGainControl: this.state.noiseCancel, channelCount: 1 }, video: false });
      return this.state.micStream;
    },
    stopStream(stream) { try { stream?.getTracks().forEach((t) => t.stop()); } catch (_) {} },
    async refreshCameras() { const devs = await navigator.mediaDevices.enumerateDevices(); this.state.camDevices = devs.filter((d) => d.kind === 'videoinput'); },
    cameraLabel(dev, idx) { return dev?.label || `camera ${idx + 1}`; },

    async startCameraByIndex(idx) {
      await this.ensureMic();
      await this.refreshCameras();
      if (this.state.camDevices.length === 0 || idx < 0) {
        this.state.camIndex = -1; this.stopStream(this.state.camStream); this.state.camStream = null; this.dom.camTxt.textContent = 'CAM: off'; this.setLed(this.dom.camLed, false); return;
      }
      idx = Math.max(0, Math.min(idx, this.state.camDevices.length - 1));
      const dev = this.state.camDevices[idx];
      const s = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { ideal: dev.deviceId } }, audio: false });
      this.stopStream(this.state.camStream); this.state.camStream = s; this.state.camIndex = idx;
      this.dom.camTxt.textContent = `CAM: ${this.cameraLabel(dev, idx)}`; this.setLed(this.dom.camLed, true, true);
    },

    async cycleCamera() {
      await this.refreshCameras();
      let next = this.state.camIndex + 1;
      if (this.state.camIndex < 0) next = 0;
      if (next >= this.state.camDevices.length) next = -1;
      await this.startCameraByIndex(next);
    },

    async startScreenWithPiP() {
      this.state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      this._screenVid = document.createElement('video'); this._screenVid.playsInline = true; this._screenVid.muted = true; this._screenVid.srcObject = this.state.screenStream; await this._screenVid.play().catch(() => {});
      this._camVid = document.createElement('video'); this._camVid.playsInline = true; this._camVid.muted = true;
      if (!this.state.camStream) await this.startCameraByIndex(0).catch(() => {});
      if (this.state.camStream) { this._camVid.srcObject = this.state.camStream; await this._camVid.play().catch(() => {}); }

      this._canvas = document.createElement('canvas');
      const ctx = this._canvas.getContext('2d');
      const st = this.state.screenStream.getVideoTracks()[0];
      const sset = st.getSettings ? st.getSettings() : {};
      this._canvas.width = sset.width || 1280; this._canvas.height = sset.height || 720;

      const draw = () => {
        ctx.drawImage(this._screenVid, 0, 0, this._canvas.width, this._canvas.height);
        if (this.state.camStream && this._camVid.readyState >= 2) {
          const pipW = Math.round(this._canvas.width * 0.22); const pipH = Math.round(pipW * 0.75); const x = this._canvas.width - pipW - 16; const y = this._canvas.height - pipH - 16;
          ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fillRect(x - 6, y - 6, pipW + 12, pipH + 12); ctx.drawImage(this._camVid, x, y, pipW, pipH);
        }
        this.state.raf = requestAnimationFrame(draw);
      };
      draw();

      const canvasStream = this._canvas.captureStream(30);
      const ms = new MediaStream();
      const v = canvasStream.getVideoTracks()[0]; if (v) ms.addTrack(v);
      await this.ensureMic(); const a = this.state.micStream?.getAudioTracks()[0]; if (a) ms.addTrack(a);
      this.state.composedStream = ms;
      st.addEventListener('ended', async () => { this.renderSystemMessage('[screen] ended by browser, returning to camera.'); await this.stopScreen(); await this.syncTracks(); });
      this.dom.screenTxt.textContent = 'SCREEN: on'; this.setLed(this.dom.screenLed, true, true);
    },

    async stopScreen() {
      this.stopStream(this.state.screenStream); this.state.screenStream = null;
      if (this.state.raf) cancelAnimationFrame(this.state.raf);
      this.state.raf = null; this.state.composedStream = null;
      if (this._screenVid) this._screenVid.srcObject = null;
      if (this._camVid) this._camVid.srcObject = null;
      this.dom.screenTxt.textContent = 'SCREEN: off'; this.setLed(this.dom.screenLed, false);
    },

    async getIceServers() {
      try { const r = await fetch(this.config.iceConfigUrl, { cache: 'no-store' }); const j = await r.json(); if (Array.isArray(j?.iceServers)) return j.iceServers; } catch (_) {}
      return [{ urls: 'stun:stun.l.google.com:19302' }];
    },

    async createPeerConnection() {
      if (this.state.pc) return;
      const pc = new RTCPeerConnection({ iceServers: await this.getIceServers(), iceCandidatePoolSize: 2 });
      this.state.pc = pc;
      this.state.txVideo = pc.addTransceiver('video', { direction: 'sendrecv' });
      this.state.txAudio = pc.addTransceiver('audio', { direction: 'sendrecv' });
      pc.onicecandidate = (e) => { if (e.candidate) this.state.socket?.emit('webrtc_ice', { candidate: e.candidate }); };
      pc.onconnectionstatechange = () => { this.dom.stPc.textContent = pc.connectionState; this.setLed(this.dom.ledRtc, pc.connectionState === 'connected', pc.connectionState === 'connecting'); };
      pc.oniceconnectionstatechange = async () => {
        this.dom.stIce.textContent = pc.iceConnectionState;
        this.setLed(this.dom.ledIce, ['connected', 'completed'].includes(pc.iceConnectionState), pc.iceConnectionState === 'checking');
        if ((pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected') && !this.state.awaitingAnswer) {
          if (typeof pc.restartIce === 'function') {
            this.dbg('ice state degraded, requesting restartIce');
            pc.restartIce();
          }
          await this.negotiate('ice-restart');
        }
      };
      pc.onnegotiationneeded = async () => this.negotiate('event');
      this.startBitrateLoop();
    },

    async negotiate(reason = 'manual') {
      const pc = this.state.pc;
      if (!pc) return;
      if (this.state.makingOffer || this.state.awaitingAnswer || pc.signalingState !== 'stable') { this.state.needsNegotiation = true; return; }
      this.state.makingOffer = true;
      try {
        const offer = await pc.createOffer({ iceRestart: reason === 'ice-restart' });
        await pc.setLocalDescription(offer);
        this.state.awaitingAnswer = true;
        this.state.socket?.emit('webrtc_offer', { sdp: pc.localDescription.sdp, type: pc.localDescription.type, reason });
      } catch (e) {
        this.state.awaitingAnswer = false;
        this.dbg('negotiate failed', e);
      } finally {
        this.state.makingOffer = false;
      }
    },

    async syncTracks(force = false) {
      await this.createPeerConnection();
      await this.ensureMic();
      const useStream = this.state.composedStream || new MediaStream([...(this.state.camStream ? this.state.camStream.getVideoTracks() : []), ...(this.state.micStream ? this.state.micStream.getAudioTracks() : [])]);
      this.state.localStream = useStream;
      this.dom.preview.srcObject = this.state.composedStream || this.state.camStream || null;
      const vTrack = useStream.getVideoTracks()[0] || null;
      const aTrack = useStream.getAudioTracks()[0] || null;
      await this.state.txVideo?.sender?.replaceTrack(vTrack);
      await this.state.txAudio?.sender?.replaceTrack(aTrack);
      if (force) await this.negotiate('initial');
    },

    startBitrateLoop() {
      if (this.state.statsInterval) return;
      this.state.lastBytes = 0; this.state.lastTs = performance.now();
      this.state.statsInterval = setInterval(async () => {
        if (!this.state.pc) return;
        try {
          const stats = await this.state.pc.getStats(); let bytes = 0;
          stats.forEach((r) => { if (r.type === 'outbound-rtp') bytes += (r.bytesSent || 0); });
          const now = performance.now(); const dt = Math.max(0.001, (now - this.state.lastTs) / 1000);
          const kbps = ((bytes - this.state.lastBytes) * 8 / 1000) / dt;
          this.state.lastBytes = bytes; this.state.lastTs = now; this.dom.stBr.textContent = `${Math.max(0, Math.round(kbps))} kbps`;
        } catch (_) {}
      }, 2000);
    },

    async sendChat() {
      if (!this.state.socket || this.state.sendLock) return;
      const t = this.dom.chatInput.value.trim();
      if (!t) return;
      this.state.sendLock = true;
      this.state.socket.emit('chat_message', { text: t, ts: Date.now(), ai_mode: this.state.aiMode });
      this.dom.chatInput.value = '';
      setTimeout(() => { this.state.sendLock = false; }, 150);
    },

    renderChatMessage(m) {
      if (!this.dom.chat) return;
      const id = m.message_id || `${m.sender}|${m.role}|${m.ts}|${m.text}`;
      if (this.state.seen.has(id)) return;
      this.state.seen.add(id);
      const role = String((m.role || m.sender || 'user')).toLowerCase();
      const label = role.includes('assistant') || role.includes('ai') ? 'AI' : role.includes('stt') ? 'STT' : role.includes('broadcast') ? 'Broadcaster' : role.includes('system') ? 'System' : 'Viewer';
      const d = document.createElement('div');
      d.className = `entry${label === 'STT' ? ' stt' : ''}`;
      d.innerHTML = `<b>[${label}]</b> <small>${new Date(m.ts || Date.now()).toLocaleTimeString()}</small><div>${this.escapeHtml(m.text || '')}</div>`;
      this.dom.chat.appendChild(d);
      this.dom.chat.scrollTop = this.dom.chat.scrollHeight;
      if (label === 'AI') this.state.lastAiText = String(m.text || '').trim();
    },

    renderSystemMessage(text) { this.renderChatMessage({ sender: 'system', role: 'system', text, ts: Date.now() }); },
    escapeHtml(str) { return String(str).replace(/[<>&]/g, (s) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[s])); },

    showSearchPane(url) {
      this.dom.searchPane?.classList.add('open');
      if (this.dom.searchFrame) this.dom.searchFrame.src = url;
    },
    hideSearchPane() { this.dom.searchPane?.classList.remove('open'); },

    async runWebSearch() {
      const q = this.dom.chatInput.value.trim();
      if (!q) return;
      const googleUrl = `https://www.google.com/search?q=${encodeURIComponent(q)}`;
      this.showSearchPane(googleUrl);
      if (this.dom.searchOpenLink) this.dom.searchOpenLink.href = googleUrl;
      this.dom.searchFallback?.classList.remove('show');
      setTimeout(() => this.dom.searchFallback?.classList.add('show'), 1800);
      this.renderSystemMessage(`[search] looking up "${q}"...`);
      this.state.socket?.emit('web_search', { query: q });
    },

    renderWebSearchResult(payload) {
      if (!payload) return;
      if (payload.ok && Array.isArray(payload.results) && payload.results.length) {
        const lines = payload.results.slice(0, 3).map((r) => `• ${r.title} — ${r.url}`).join('\n');
        this.renderSystemMessage(`[search:${payload.provider}] ${payload.query}\n${lines}`);
      } else if (payload.query) {
        this.renderSystemMessage(`[search] ${payload.message || 'provider unavailable'} for "${payload.query}"`);
      }
    },

    async speakLastAi() { if (this.state.lastAiText) this.state.socket?.emit('speak_last_ai', { text: this.state.lastAiText }); },
    playUrl(url) { try { const a = new Audio(url); a.play().catch(() => {}); } catch (_) {} },

    async startStt() {
      if (this.state.sttRec) return;
      await this.ensureMic();
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
      this.state.sttRec = new MediaRecorder(this.state.micStream, mime ? { mimeType: mime } : undefined);
      this.state.sttRec.ondataavailable = async (ev) => {
        if (!ev.data?.size) return;
        const ab = await ev.data.arrayBuffer();
        this.state.socket?.emit('stt_chunk', { b64: b64FromArrayBuffer(ab), mime: ev.data.type || 'audio/webm', ts: Date.now() });
      };
      this.state.sttRec.start(500);
      this.state.sttEnabled = true;
      this.dom.sttTxt.textContent = 'STT on'; this.dom.stStt.textContent = 'on'; this.setLed(this.dom.sttLed, true, true); this.setLed(this.dom.ledStt, true, true);
      this.state.socket?.emit('set_room_settings', { stt_enabled: true });
      this.state.socket?.emit('ai_stt_enable', { enabled: true });
    },
    stopStt() {
      try { this.state.sttRec?.stop(); } catch (_) {}
      this.state.sttRec = null; this.state.sttEnabled = false;
      this.dom.sttTxt.textContent = 'STT off'; this.dom.stStt.textContent = 'off'; this.setLed(this.dom.sttLed, false); this.setLed(this.dom.ledStt, false);
      this.state.socket?.emit('set_room_settings', { stt_enabled: false });
      this.state.socket?.emit('ai_stt_disable', { enabled: false });
    },

    setLed(el, on, blink = false) { if (!el) return; el.className = `led ${on ? `g${blink ? ' blink' : ''}` : 'r'}`; },
    setAiMode(mode, emit = true) {
      this.state.aiMode = mode === 'idle' ? 'idle' : 'active';
      this.dom.stAi.textContent = this.state.aiMode;
      this.setLed(this.dom.ledAi, this.state.aiMode === 'active', this.state.aiMode === 'active');
      this.dom.aiIdleBtn?.classList.toggle('active', this.state.aiMode === 'idle');
      this.dom.aiActiveBtn?.classList.toggle('active', this.state.aiMode === 'active');
      if (emit) this.state.socket?.emit('set_room_settings', { ai_enabled: true, ai_mode: this.state.aiMode });
    },
    setSst(on) {
      this.state.sstEnabled = !!on;
      this.dom.sstTxt.textContent = `SST: ${this.state.sstEnabled ? 'on' : 'off'}`;
      this.setLed(this.dom.sstLed, this.state.sstEnabled, this.state.sstEnabled);
    },
    setTtsMonitor(on) {
      this.state.ttsMonitor = !!on;
      this.dom.ttsMonTxt.textContent = `Hear AI voice: ${this.state.ttsMonitor ? 'on' : 'off'}`;
      this.setLed(this.dom.ttsMonLed, this.state.ttsMonitor, this.state.ttsMonitor);
      this.state.socket?.emit('set_room_settings', { tts_enabled: this.state.ttsMonitor });
    },
    setNoiseCancel(on) {
      this.state.noiseCancel = !!on;
      this.dom.ncTxt.textContent = `NoiseCancel: ${this.state.noiseCancel ? 'on' : 'off'}`;
      this.setLed(this.dom.ncLed, this.state.noiseCancel, this.state.noiseCancel);
      this.stopStream(this.state.micStream); this.state.micStream = null;
      this.ensureMic().then(() => this.syncTracks()).catch(() => {});
    },

    async cleanup() {
      try { this.stopStt(); } catch (_) {}
      this.stopStream(this.state.camStream); this.stopStream(this.state.screenStream); this.stopStream(this.state.micStream);
      if (this.state.statsInterval) clearInterval(this.state.statsInterval);
      try { await this.state.pc?.close(); } catch (_) {}
      try { this.state.socket?.disconnect(); } catch (_) {}
    },
  };

  window.BroadcastApp = App;
  window.addEventListener('DOMContentLoaded', () => App.init(), { once: true });
  window.addEventListener('beforeunload', () => App.cleanup(), { once: true });
})();
