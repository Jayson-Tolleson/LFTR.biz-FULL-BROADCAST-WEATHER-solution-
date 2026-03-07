// static/js/broadcast.js
(() => {
  // 1) constants / debug helpers
  const DEFAULT_CONFIG = {
    socketPath: '/socket.io',
    iceConfigUrl: '/webrtc/ice-config',
    aiChatUrl: '/ai/chat',
    aiTtsUrl: '/ai/tts',
    aiWebSearchUrl: '/ai/websearch',
    role: 'broadcast',
    debug: true,
  };

  function dbg(...args) {
    if (BroadcastApp.config.debug) {
      console.log('[broadcast]', ...args);
    }
  }

  // 2) small utility helpers
  function safeId(id) {
    return document.getElementById(id);
  }

  function b64FromArrayBuffer(ab) {
    const bytes = new Uint8Array(ab);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  }

  const BroadcastApp = {
    // 3) config
    config: { ...DEFAULT_CONFIG },

    // 4) state
    state: {
      room: 'default',
      socket: null,
      pc: null,
      txVideo: null,
      txAudio: null,
      localStream: null,
      micStream: null,
      camStream: null,
      screenStream: null,
      composedStream: null,
      sttRec: null,
      sttEnabled: false,
      aiActive: false,
      ttsMonitor: false,
      noiseCancel: false,
      camDevices: [],
      camIndex: -1,
      seen: new Set(),
      lastAiText: '',
      connected: false,
      mediaReady: false,
      busy: false,
      destroyed: false,
      makingOffer: false,
      raf: null,
      statsInterval: null,
      lastBytes: 0,
      lastTs: 0,
    },

    // 5) dom
    dom: {},

    // 6) initialization methods
    init() {
      if (this.state.destroyed) return;
      this.loadConfig();
      this.parseRoom();
      this.cacheDom();
      this.bindEvents();
      this.connectSocket();
      dbg('initialized', { room: this.state.room });
    },

    loadConfig() {
      this.config = { ...DEFAULT_CONFIG, ...(window.BROADCAST_CONFIG || {}) };
    },

    parseRoom() {
      const params = new URLSearchParams(window.location.search);
      this.state.room = (params.get('room') || 'default').trim() || 'default';
    },

    cacheDom() {
      this.dom = {
        preview: safeId('preview'),
        chat: safeId('chat'),
        chatInput: safeId('chatInput'),
        roomStatus: safeId('roomStatus'),
        stRoom: safeId('stRoom'),
        stSock: safeId('stSock'),
        stPc: safeId('stPc'),
        stIce: safeId('stIce'),
        stStt: safeId('stStt'),
        stAi: safeId('stAi'),
        stBr: safeId('stBr'),

        ledSock: safeId('ledSock'),
        ledRtc: safeId('ledRtc'),
        ledIce: safeId('ledIce'),
        ledStt: safeId('ledStt'),
        ledAi: safeId('ledAi'),

        camBtn: safeId('camBtn'),
        camLed: safeId('camLed'),
        camTxt: safeId('camTxt'),
        screenBtn: safeId('screenBtn'),
        screenLed: safeId('screenLed'),
        screenTxt: safeId('screenTxt'),
        ncBtn: safeId('ncBtn'),
        ncLed: safeId('ncLed'),
        ncTxt: safeId('ncTxt'),
        sttBtn: safeId('sttBtn'),
        sttLed: safeId('sttLed'),
        sttTxt: safeId('sttTxt'),
        aiBtn: safeId('aiBtn'),
        aiLed: safeId('aiLed'),
        aiTxt: safeId('aiTxt'),
        ttsMonBtn: safeId('ttsMonBtn'),
        ttsMonLed: safeId('ttsMonLed'),
        ttsMonTxt: safeId('ttsMonTxt'),

        sendBtn: safeId('sendBtn'),
        attachBtn: safeId('attachBtn'),
        webBtn: safeId('webBtn'),
        speakBtn: safeId('speakBtn'),
        file: safeId('file'),
      };

      if (this.dom.stRoom) {
        this.dom.stRoom.textContent = this.state.room;
      }
    },

    // 7) DOM event binding methods
    bindEvents() {
      if (this.dom.sendBtn) {
        this.dom.sendBtn.addEventListener('click', () => this.sendChat());
      }

      if (this.dom.chatInput) {
        this.dom.chatInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            this.sendChat();
          }
        });
      }

      if (this.dom.camBtn) {
        this.dom.camBtn.addEventListener('click', async () => {
          try {
            await this.cycleCamera();
            if (this.state.screenStream && this._camVid) {
              this._camVid.srcObject = this.state.camStream;
              await this._camVid.play().catch(() => {});
            }
            await this.syncTracks();
          } catch (err) {
            this.renderSystemMessage('[camera] failed to switch (permission denied?)');
          }
        });
      }

      if (this.dom.screenBtn) {
        this.dom.screenBtn.addEventListener('click', async () => {
          if (this.state.screenStream) {
            await this.stopScreen();
            await this.syncTracks();
            return;
          }
          try {
            await this.startScreenWithPiP();
            await this.syncTracks();
          } catch (err) {
            dbg('screen start failed', err);
            this.renderSystemMessage('[screen] failed — browser blocked screen capture or user canceled.');
            await this.stopScreen();
            await this.syncTracks();
          }
        });
      }

      if (this.dom.aiBtn) this.dom.aiBtn.addEventListener('click', () => this.setAiActive(!this.state.aiActive));
      if (this.dom.ncBtn) this.dom.ncBtn.addEventListener('click', () => this.setNoiseCancel(!this.state.noiseCancel));
      if (this.dom.sttBtn) this.dom.sttBtn.addEventListener('click', () => (this.state.sttEnabled ? this.stopStt() : this.startStt()));
      if (this.dom.ttsMonBtn) this.dom.ttsMonBtn.addEventListener('click', () => this.setTtsMonitor(!this.state.ttsMonitor));
      if (this.dom.speakBtn) this.dom.speakBtn.addEventListener('click', () => this.speakLastAi());
      if (this.dom.attachBtn && this.dom.file) this.dom.attachBtn.addEventListener('click', () => this.dom.file.click());
      if (this.dom.webBtn) this.dom.webBtn.addEventListener('click', () => this.runWebSearch());

      if (this.dom.file) {
        this.dom.file.addEventListener('change', async (e) => {
          const f = e.target.files && e.target.files[0];
          if (!f || !this.state.socket) return;
          const ab = await f.arrayBuffer();
          this.state.socket.emit('upload_file', {
            name: f.name,
            mime: f.type,
            content_base64: b64FromArrayBuffer(ab),
            text: `uploaded ${f.name}`,
            upload_type: 'location_video',
            locationId: this.state.room,
          });
        });
      }
    },

    // 8) socket methods
    connectSocket() {
      const socket = io(window.location.origin, {
        path: this.config.socketPath,
        transports: ['websocket'],
        upgrade: false,
        query: { room: this.state.room, role: this.config.role },
      });

      this.state.socket = socket;
      this.bindSocketEvents();
    },

    bindSocketEvents() {
      const s = this.state.socket;
      if (!s) return;

      s.on('connect', async () => {
        dbg('socket connect', this.state.room);
        this.state.connected = true;
        this.setLed(this.dom.ledSock, true, true);
        if (this.dom.stSock) this.dom.stSock.textContent = 'connected';

        this.setNoiseCancel(false);
        this.setAiActive(true);
        this.setTtsMonitor(false);

        try {
          await this.initMedia();
        } catch (err) {
          this.renderSystemMessage('[mic] permission needed — allow Microphone for this site, then reload.');
          return;
        }

        await this.createPeerConnection();

        try {
          await this.refreshCameras();
          if (this.state.camDevices.length > 0) {
            await this.startCameraByIndex(0);
          } else if (this.dom.camTxt) {
            this.dom.camTxt.textContent = 'CAM: none';
            this.setLed(this.dom.camLed, false);
          }
        } catch (err) {
          if (this.dom.camTxt) this.dom.camTxt.textContent = 'CAM: permission needed';
          this.setLed(this.dom.camLed, false);
          this.renderSystemMessage('[camera] permission needed — click CAM to retry.');
        }

        await this.syncTracks();
        await this.startStt().catch(() => {});
      });

      s.on('disconnect', () => {
        dbg('socket disconnect');
        this.state.connected = false;
        this.setLed(this.dom.ledSock, false);
        if (this.dom.stSock) this.dom.stSock.textContent = 'disconnected';
      });

      s.on('webrtc_answer', async (ans) => {
        try {
          await this.createPeerConnection();
          if (this.state.pc) {
            await this.state.pc.setRemoteDescription(ans);
          }
        } catch (err) {
          dbg('set remote answer failed', err);
        }
      });

      s.on('webrtc_ice_server', async (p) => {
        try {
          if (this.state.pc && p && p.candidate) {
            await this.state.pc.addIceCandidate(p.candidate || p);
          }
        } catch (err) {
          dbg('remote ice add failed', err);
        }
      });

      s.on('chat_message', (msg) => this.renderChatMessage(msg));
      s.on('stt_text', (p) => this.renderChatMessage({ sender: 'stt', role: 'broadcaster_stt', text: p.text, ts: p.ts || Date.now() }));
      s.on('stage_state', (p) => {
        if (!p) return;
        if (p.mode === 'upload' && p.latestUploadUrl) {
          this.renderSystemMessage('[stage] using latest upload for PUBLIC ACCESS');
        }
      });

      s.on('room_status', (p) => {
        if (!p || !this.dom.roomStatus) return;
        this.dom.roomStatus.textContent = `viewers:${p.viewer_count || 0} broadcaster:${p.broadcaster_present ? 'yes' : 'no'}`;
      });
      s.on('ai_tts_audio', (p) => {
        if (p && p.url && this.state.ttsMonitor) {
          this.playUrl(p.url);
        }
      });
    },

    // 9) media methods
    async initMedia() {
      await this.ensureMic();
      this.state.mediaReady = true;
      this.ensurePreview();
    },

    async ensureMic() {
      if (this.state.micStream) return this.state.micStream;
      const constraints = {
        audio: {
          echoCancellation: this.state.noiseCancel,
          noiseSuppression: this.state.noiseCancel,
          autoGainControl: this.state.noiseCancel,
          channelCount: 1,
        },
        video: false,
      };
      this.state.micStream = await navigator.mediaDevices.getUserMedia(constraints);
      return this.state.micStream;
    },

    ensurePreview() {
      if (!this.dom.preview) return;
      this.dom.preview.srcObject = this.state.composedStream ? this.state.composedStream : (this.state.camStream || null);
    },

    stopStream(stream) {
      try {
        if (stream) stream.getTracks().forEach((t) => t.stop());
      } catch (_e) {
        // noop
      }
    },

    async refreshCameras() {
      const devs = await navigator.mediaDevices.enumerateDevices();
      this.state.camDevices = devs.filter((d) => d.kind === 'videoinput');
    },

    cameraLabel(dev, idx) {
      const name = (dev && dev.label) ? dev.label : `camera ${idx + 1}`;
      const lower = name.toLowerCase();
      if (lower.includes('back') || lower.includes('rear')) return `${name} (rear)`;
      if (lower.includes('front')) return `${name} (front)`;
      return name;
    },

    async startCameraByIndex(idx) {
      try {
        await this.ensureMic();
      } catch (err) {
        if (this.dom.camTxt) this.dom.camTxt.textContent = 'CAM: permission needed';
        this.setLed(this.dom.camLed, false);
        this.renderSystemMessage('[camera] permission needed — allow Microphone first, then click CAM again.');
        throw err;
      }

      await this.refreshCameras();

      if (this.state.camDevices.length === 0) {
        this.state.camIndex = -1;
        if (this.dom.camTxt) this.dom.camTxt.textContent = 'CAM: none';
        this.setLed(this.dom.camLed, false);
        this.stopStream(this.state.camStream);
        this.state.camStream = null;
        return;
      }

      if (idx < 0) {
        this.state.camIndex = -1;
        if (this.dom.camTxt) this.dom.camTxt.textContent = 'CAM: off';
        this.setLed(this.dom.camLed, false);
        this.stopStream(this.state.camStream);
        this.state.camStream = null;
        return;
      }

      idx = Math.max(0, Math.min(idx, this.state.camDevices.length - 1));
      const dev = this.state.camDevices[idx];

      let s = null;
      try {
        s = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { ideal: dev.deviceId } }, audio: false });
      } catch (_e) {
        s = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { exact: dev.deviceId } }, audio: false });
      }

      this.stopStream(this.state.camStream);
      this.state.camStream = s;
      this.state.camIndex = idx;

      if (this.dom.camTxt) this.dom.camTxt.textContent = `CAM: ${this.cameraLabel(dev, idx)}`;
      this.setLed(this.dom.camLed, true, true);
    },

    async cycleCamera() {
      await this.refreshCameras();
      if (this.state.camDevices.length === 0) {
        await this.startCameraByIndex(-1);
        return;
      }
      let next;
      if (this.state.camIndex === -1) next = 0;
      else {
        next = this.state.camIndex + 1;
        if (next >= this.state.camDevices.length) next = -1;
      }
      await this.startCameraByIndex(next);
    },

    async startScreenWithPiP() {
      this.state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      this._screenVid = document.createElement('video');
      this._screenVid.playsInline = true;
      this._screenVid.muted = true;
      this._screenVid.srcObject = this.state.screenStream;
      await this._screenVid.play().catch(() => {});

      this._camVid = document.createElement('video');
      this._camVid.playsInline = true;
      this._camVid.muted = true;

      await this.refreshCameras();
      if (!this.state.camStream && this.state.camDevices.length > 0) {
        try {
          await this.startCameraByIndex(0);
        } catch (_e) {
          // best effort
        }
      }

      if (this.state.camStream) {
        this._camVid.srcObject = this.state.camStream;
        await this._camVid.play().catch(() => {});
      }

      this._canvas = document.createElement('canvas');
      const ctx = this._canvas.getContext('2d');
      const st = this.state.screenStream.getVideoTracks()[0];
      const sset = st.getSettings ? st.getSettings() : {};
      this._canvas.width = sset.width || 1280;
      this._canvas.height = sset.height || 720;

      const draw = () => {
        try {
          ctx.drawImage(this._screenVid, 0, 0, this._canvas.width, this._canvas.height);
          if (this.state.camStream && this._camVid.readyState >= 2) {
            const pipW = Math.round(this._canvas.width * 0.22);
            const pipH = Math.round(pipW * 0.75);
            const x = this._canvas.width - pipW - 16;
            const y = this._canvas.height - pipH - 16;
            ctx.fillStyle = 'rgba(0,0,0,0.35)';
            ctx.fillRect(x - 6, y - 6, pipW + 12, pipH + 12);
            ctx.drawImage(this._camVid, x, y, pipW, pipH);
          }
        } catch (_e) {
          // noop
        }
        this.state.raf = requestAnimationFrame(draw);
      };
      draw();

      const canvasStream = this._canvas.captureStream(30);
      const ms = new MediaStream();
      const v = canvasStream.getVideoTracks()[0];
      if (v) ms.addTrack(v);

      await this.ensureMic();
      const a = this.state.micStream && this.state.micStream.getAudioTracks()[0];
      if (a) ms.addTrack(a);
      this.state.composedStream = ms;

      st.addEventListener('ended', async () => {
        await this.stopScreen();
        await this.syncTracks();
      });

      if (this.dom.screenTxt) this.dom.screenTxt.textContent = 'SCREEN: on (PiP)';
      this.setLed(this.dom.screenLed, true, true);
    },

    async stopScreen() {
      this.stopStream(this.state.screenStream);
      this.state.screenStream = null;
      this.stopCompositor();
      if (this.dom.screenTxt) this.dom.screenTxt.textContent = 'SCREEN: off';
      this.setLed(this.dom.screenLed, false);
    },

    stopCompositor() {
      if (this.state.raf) {
        cancelAnimationFrame(this.state.raf);
        this.state.raf = null;
      }
      this.state.composedStream = null;
      if (this._screenVid) this._screenVid.srcObject = null;
      if (this._camVid) this._camVid.srcObject = null;
    },

    // 10) WebRTC methods
    async createPeerConnection() {
      if (this.state.pc) return;
      const iceServers = await this.getIceServers();
      const pc = new RTCPeerConnection({ iceServers });
      this.state.pc = pc;
      this.state.txVideo = pc.addTransceiver('video', { direction: 'sendrecv' });
      this.state.txAudio = pc.addTransceiver('audio', { direction: 'sendrecv' });
      this.bindPeerEvents(pc);
      this.startBitrateLoop();
    },

    bindPeerEvents(pc) {
      pc.onicecandidate = (e) => {
        if (e.candidate && this.state.socket) {
          this.state.socket.emit('webrtc_ice', { candidate: e.candidate });
        }
      };
      pc.onconnectionstatechange = () => {
        if (this.dom.stPc) this.dom.stPc.textContent = pc.connectionState;
        this.setLed(this.dom.ledRtc, pc.connectionState === 'connected', pc.connectionState === 'connecting');
        dbg('pc connectionState', pc.connectionState);
      };
      pc.oniceconnectionstatechange = () => {
        if (this.dom.stIce) this.dom.stIce.textContent = pc.iceConnectionState;
        this.setLed(
          this.dom.ledIce,
          pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed',
          pc.iceConnectionState === 'checking',
        );
        dbg('pc iceConnectionState', pc.iceConnectionState);
      };
    },

    async negotiate() {
      const pc = this.state.pc;
      if (!pc || this.state.makingOffer) return;

      if (pc.signalingState !== 'stable') {
        await new Promise((r) => setTimeout(r, 150));
        if (pc.signalingState !== 'stable') return;
      }

      this.state.makingOffer = true;
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        if (this.state.socket) {
          this.state.socket.emit('webrtc_offer', {
            sdp: pc.localDescription.sdp,
            type: pc.localDescription.type,
          });
        }
      } catch (err) {
        dbg('negotiate failed', err);
      } finally {
        this.state.makingOffer = false;
      }
    },

    async syncTracks() {
      await this.createPeerConnection();
      await this.ensureMic();

      const useStream = this.state.composedStream || new MediaStream([
        ...(this.state.camStream ? this.state.camStream.getVideoTracks() : []),
        ...(this.state.micStream ? this.state.micStream.getAudioTracks() : []),
      ]);

      this.state.localStream = useStream;
      this.ensurePreview();

      const vTrack = useStream.getVideoTracks()[0] || null;
      const aTrack = useStream.getAudioTracks()[0] || null;

      if (this.state.txVideo && this.state.txVideo.sender) await this.state.txVideo.sender.replaceTrack(vTrack);
      if (this.state.txAudio && this.state.txAudio.sender) await this.state.txAudio.sender.replaceTrack(aTrack);

      await this.negotiate();
    },

    async closePeerConnection() {
      if (!this.state.pc) return;
      try {
        await this.state.pc.close();
      } catch (_e) {
        // noop
      }
      this.state.pc = null;
      this.state.txVideo = null;
      this.state.txAudio = null;
      if (this.dom.stPc) this.dom.stPc.textContent = 'closed';
      if (this.dom.stIce) this.dom.stIce.textContent = 'closed';
      this.setLed(this.dom.ledRtc, false);
      this.setLed(this.dom.ledIce, false);
    },

    async getIceServers() {
      try {
        const r = await fetch(this.config.iceConfigUrl, { cache: 'no-store' });
        if (!r.ok) throw new Error(`bad status ${r.status}`);
        const j = await r.json();
        if (j && j.iceServers && Array.isArray(j.iceServers)) return j.iceServers;
      } catch (err) {
        dbg('ice-config fetch failed; using STUN only', err);
      }
      return [{ urls: 'stun:stun.l.google.com:19302' }];
    },

    startBitrateLoop() {
      if (this.state.statsInterval) return;
      this.state.lastBytes = 0;
      this.state.lastTs = performance.now();
      this.state.statsInterval = setInterval(async () => {
        if (!this.state.pc || !this.dom.stBr) return;
        try {
          const stats = await this.state.pc.getStats();
          let bytes = 0;
          stats.forEach((r) => {
            if (r.type === 'outbound-rtp') bytes += (r.bytesSent || 0);
          });
          const now = performance.now();
          const dt = Math.max(0.001, (now - this.state.lastTs) / 1000);
          const kbps = ((bytes - this.state.lastBytes) * 8 / 1000) / dt;
          this.state.lastBytes = bytes;
          this.state.lastTs = now;
          this.dom.stBr.textContent = `${Math.max(0, Math.round(kbps))} kbps`;
        } catch (_e) {
          // noop
        }
      }, 2000);
    },

    // 11) chat + AI methods
    sendChat() {
      if (!this.state.socket || !this.dom.chatInput) return;
      const t = this.dom.chatInput.value.trim();
      if (!t) return;
      this.state.socket.emit('chat_message', { text: t, ts: Date.now() });
      this.dom.chatInput.value = '';
    },

    renderChatMessage(m) {
      if (!this.dom.chat) return;
      const id = m.message_id || `${m.sender}|${m.role}|${m.ts}|${m.text}`;
      if (this.state.seen.has(id)) return;
      this.state.seen.add(id);
      if (this.state.seen.size > 1200) {
        const arr = [...this.state.seen].slice(-400);
        this.state.seen = new Set(arr);
      }

      const lbl = this.labelForMessage(m);
      const d = document.createElement('div');
      d.className = `entry${lbl === 'STT' ? ' stt' : ''}`;
      d.innerHTML = `<b>[${lbl}]</b> <small>${new Date(m.ts || Date.now()).toLocaleTimeString()}</small><div>${this.escapeHtml(m.text || '')}</div>`;
      this.dom.chat.appendChild(d);
      this.dom.chat.scrollTop = this.dom.chat.scrollHeight;

      if (lbl === 'AI') {
        this.state.lastAiText = String(m.text || '').trim();
      }
    },

    renderSystemMessage(text) {
      this.renderChatMessage({ sender: 'system', role: 'system', text, ts: Date.now() });
    },

    labelForMessage(m) {
      const r = String((m.role || m.sender || '')).toLowerCase();
      if (r.includes('assistant') || r.includes('ai')) return 'AI';
      if (r.includes('broadcaster_stt') || r === 'stt') return 'STT';
      if (r.includes('broadcaster')) return 'Broadcaster';
      if (r.includes('viewer') || r.includes('watch')) return 'Viewer';
      if (r === 'system') return 'System';
      return 'User';
    },

    escapeHtml(str) {
      return String(str).replace(/[<>&]/g, (s) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[s]));
    },

    async runWebSearch() {
      if (!this.state.socket || !this.dom.chatInput) return;
      const q = this.dom.chatInput.value.trim();
      if (!q) return;
      this.state.socket.emit('web_search', { query: q });
    },

    async requestAiReply(_prompt) {
      // Existing page flow is socket-driven for AI responses; keep contract unchanged.
      return null;
    },

    async speakLastAi() {
      if (!this.state.socket || !this.state.lastAiText) return;
      this.state.socket.emit('speak_last_ai', { text: this.state.lastAiText });
    },

    playUrl(url) {
      try {
        const a = new Audio(url);
        a.volume = 1.0;
        a.play().catch(() => {});
      } catch (_e) {
        // noop
      }
    },

    async startStt() {
      if (this.state.sttRec) return;
      await this.ensureMic();
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
      this.state.sttRec = new MediaRecorder(this.state.micStream, mime ? { mimeType: mime } : undefined);
      this.state.sttRec.ondataavailable = async (ev) => {
        if (!ev.data || !ev.data.size || !this.state.socket) return;
        const ab = await ev.data.arrayBuffer();
        this.state.socket.emit('stt_chunk', { b64: b64FromArrayBuffer(ab), mime: ev.data.type || 'audio/webm', ts: Date.now() });
      };
      this.state.sttRec.start(500);
      this.state.sttEnabled = true;
      if (this.dom.sttTxt) this.dom.sttTxt.textContent = 'STT: on';
      if (this.dom.stStt) this.dom.stStt.textContent = 'on';
      this.setLed(this.dom.sttLed, true, true);
      this.setLed(this.dom.ledStt, true, true);
      if (this.state.socket) {
        this.state.socket.emit('set_room_settings', { stt_enabled: true });
        this.state.socket.emit('stt_toggle', { enabled: true });
      }
      dbg('stt started');
    },

    stopStt() {
      try {
        if (this.state.sttRec) this.state.sttRec.stop();
      } catch (_e) {
        // noop
      }
      this.state.sttRec = null;
      this.state.sttEnabled = false;
      if (this.dom.sttTxt) this.dom.sttTxt.textContent = 'STT: off';
      if (this.dom.stStt) this.dom.stStt.textContent = 'off';
      this.setLed(this.dom.sttLed, false);
      this.setLed(this.dom.ledStt, false);
      if (this.state.socket) {
        this.state.socket.emit('set_room_settings', { stt_enabled: false });
        this.state.socket.emit('stt_toggle', { enabled: false });
      }
      dbg('stt stopped');
    },

    // 12) UI/status methods
    setStatus(text, kind = 'info') {
      dbg('status', kind, text);
      if (this.dom.roomStatus && text) this.dom.roomStatus.textContent = text;
    },

    setLed(el, on, blink = false) {
      if (!el) return;
      el.className = `led ${on ? `g${blink ? ' blink' : ''}` : 'r'}`;
    },

    setBusy(flag) {
      this.state.busy = Boolean(flag);
      if (this.dom.sendBtn) this.dom.sendBtn.disabled = this.state.busy;
      if (this.dom.webBtn) this.dom.webBtn.disabled = this.state.busy;
    },

    setAiActive(on) {
      this.state.aiActive = Boolean(on);
      if (this.dom.aiTxt) this.dom.aiTxt.textContent = `AI: ${this.state.aiActive ? 'active' : 'idle'}`;
      if (this.dom.stAi) this.dom.stAi.textContent = this.state.aiActive ? 'active' : 'idle';
      this.setLed(this.dom.aiLed, this.state.aiActive, this.state.aiActive);
      this.setLed(this.dom.ledAi, this.state.aiActive, this.state.aiActive);
      if (this.state.socket) {
        this.state.socket.emit('set_room_settings', { ai_enabled: this.state.aiActive });
        this.state.socket.emit('ai_settings_set', { ai_power: this.state.aiActive, ai_mode: this.state.aiActive ? 'active' : 'idle' });
      }
    },

    setTtsMonitor(on) {
      this.state.ttsMonitor = Boolean(on);
      if (this.dom.ttsMonTxt) this.dom.ttsMonTxt.textContent = `Hear AI voice: ${this.state.ttsMonitor ? 'on' : 'off'}`;
      this.setLed(this.dom.ttsMonLed, this.state.ttsMonitor, this.state.ttsMonitor);
      if (this.state.socket) this.state.socket.emit('set_room_settings', { tts_enabled: this.state.ttsMonitor });
    },

    setNoiseCancel(on) {
      this.state.noiseCancel = Boolean(on);
      if (this.dom.ncTxt) this.dom.ncTxt.textContent = `NoiseCancel: ${this.state.noiseCancel ? 'on' : 'off'}`;
      this.setLed(this.dom.ncLed, this.state.noiseCancel, this.state.noiseCancel);
      if (this.state.micStream) {
        this.stopStream(this.state.micStream);
        this.state.micStream = null;
      }
      this.ensureMic().then(() => this.syncTracks()).catch((e) => dbg('noise cancel mic refresh failed', e));
    },

    // 13) cleanup methods
    async cleanup() {
      if (this.state.destroyed) return;
      this.state.destroyed = true;
      dbg('cleanup start');

      this.stopStt();
      this.stopStream(this.state.camStream);
      this.stopStream(this.state.screenStream);
      this.stopStream(this.state.micStream);
      this.stopCompositor();

      if (this.state.statsInterval) {
        clearInterval(this.state.statsInterval);
        this.state.statsInterval = null;
      }

      await this.closePeerConnection();

      if (this.state.socket) {
        try { this.state.socket.disconnect(); } catch (_e) { /* noop */ }
        this.state.socket = null;
      }

      dbg('cleanup done');
    },
  };

  // 14) bootstrapping code
  window.BroadcastApp = BroadcastApp;
  window.addEventListener('DOMContentLoaded', () => BroadcastApp.init(), { once: true });
  window.addEventListener('beforeunload', () => BroadcastApp.cleanup(), { once: true });
})();
