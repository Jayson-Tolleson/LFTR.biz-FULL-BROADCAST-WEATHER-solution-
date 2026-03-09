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
    stSock: document.getElementById('stSock'),
    ledSock: document.getElementById('ledSock'),
    stPc: document.getElementById('stPc'),
    stIce: document.getElementById('stIce'),
    stRoom: document.getElementById('stRoom'),
    camBtn: document.getElementById('camBtn'),
  };

  const state = {
    room: new URLSearchParams(location.search).get('room') || 'default',
    camStream: null,
    pc: null,
    chatWs: null,
    signalWs: null,
  };
  dom.stRoom && (dom.stRoom.textContent = state.room);

  function setLed(el, on) {
    if (!el) return;
    el.classList.remove('r', 'g');
    el.classList.add(on ? 'g' : 'r');
  }

  function appendChat(msg) {
    if (!dom.chat) return;
    const d = document.createElement('div');
    d.className = 'entry';
    const who = msg.user || 'system';
    const text = (msg.payload && msg.payload.text) || msg.text || '';
    d.innerHTML = `<b>[${who}]</b><div>${String(text).replace(/[<>&]/g, (s)=>({"<":"&lt;",">":"&gt;","&":"&amp;"}[s]))}</div>`;
    dom.chat.appendChild(d);
    dom.chat.scrollTop = dom.chat.scrollHeight;
  }

  function sendWs(ws, type, payload) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type, payload }));
  }

  function connectChat() {
    const ws = new WebSocket(`${wsBase}/ws/chat`);
    state.chatWs = ws;
    ws.onopen = () => {
      dom.stSock && (dom.stSock.textContent = 'connected');
      setLed(dom.ledSock, true);
    };
    ws.onmessage = (ev) => handleChatMessage(ev.data);
    ws.onclose = () => {
      dom.stSock && (dom.stSock.textContent = 'reconnecting');
      setLed(dom.ledSock, false);
      setTimeout(connectChat, 2000);
    };
  }

  function connectSignal() {
    const ws = new WebSocket(`${wsBase}/ws/broadcast`);
    state.signalWs = ws;
    ws.onopen = async () => {
      await ensurePc();
      await negotiate('initial');
    };
    ws.onmessage = async (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === 'webrtc_answer' && msg.payload?.sdp && state.pc) {
        await state.pc.setRemoteDescription(msg.payload);
      }
      if (msg.type === 'webrtc_ice' && msg.payload?.candidate && state.pc) {
        try { await state.pc.addIceCandidate(msg.payload.candidate); } catch {}
      }
      if (msg.type === 'viewer_count') {
        dom.roomStatus && (dom.roomStatus.textContent = `viewers: ${msg.payload?.count ?? 0}`);
      }
    };
    ws.onclose = () => setTimeout(connectSignal, 2000);
  }

  function handleChatMessage(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    if (msg.type === 'chat' || msg.type === 'ai' || msg.type === 'transcript') {
      appendChat(msg.payload ? { ...msg.payload, user: msg.user || msg.payload.user } : msg);
    }
    if (msg.type === 'viewer_count') {
      dom.roomStatus && (dom.roomStatus.textContent = `viewers: ${msg.payload?.count ?? 0}`);
    }
  }

  async function ensureCam() {
    if (state.camStream) return;
    state.camStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    if (dom.preview) dom.preview.srcObject = state.camStream;
  }

  async function ensurePc() {
    if (state.pc) return;
    const iceCfg = await fetch('/webrtc/ice-config').then((r) => r.json()).catch(() => ({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] }));
    const pc = new RTCPeerConnection({ iceServers: iceCfg.iceServers || [{ urls: 'stun:stun.l.google.com:19302' }] });
    state.pc = pc;
    dom.stPc && (dom.stPc.textContent = 'new');

    pc.onicecandidate = (e) => { if (e.candidate) sendWs(state.signalWs, 'webrtc_ice', { candidate: e.candidate }); };
    pc.onconnectionstatechange = () => { dom.stPc && (dom.stPc.textContent = pc.connectionState); };
    pc.oniceconnectionstatechange = () => { dom.stIce && (dom.stIce.textContent = pc.iceConnectionState); };

    await ensureCam();
    for (const track of state.camStream.getTracks()) pc.addTrack(track, state.camStream);
  }

  async function negotiate(reason) {
    if (!state.pc) return;
    const offer = await state.pc.createOffer();
    await state.pc.setLocalDescription(offer);
    sendWs(state.signalWs, 'webrtc_offer', { sdp: offer.sdp, type: offer.type, reason });
  }

  dom.sendBtn?.addEventListener('click', () => {
    const text = dom.chatInput?.value?.trim();
    if (!text) return;
    sendWs(state.chatWs, 'chat', { user: 'broadcaster', text, room: state.room });
    dom.chatInput.value = '';
  });

  dom.camBtn?.addEventListener('click', async () => {
    await ensureCam();
    appendChat({ user: 'system', text: 'Camera active' });
  });

  connectChat();
  connectSignal();
})();
