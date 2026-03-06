// static/js/broadcast.js
(function () {
  const cfg = window.BROADCAST_CONFIG || {};
  const DEBUG = Boolean(cfg.debug);
  const dbg = (...args) => { if (DEBUG) console.log('[broadcast]', ...args); };

  const BroadcastApp = {
    initialized: false,
    init() {
      if (this.initialized) return;
      this.initialized = true;
      dbg('init');
  const params=new URLSearchParams(location.search);
  const room=(params.get('room')||'default').trim() || 'default';
  document.getElementById('stRoom').textContent = room;

  const socket=io(location.origin,{
    path: cfg.socketPath || '/socket.io',
    transports:['websocket'],
    upgrade:false,
    query:{room,role:'broadcast'}
  });

  // UI
  const preview=document.getElementById('preview');
  const chatEl=document.getElementById('chat');
  const input=document.getElementById('chatInput');

  const ledSock=document.getElementById('ledSock'), stSock=document.getElementById('stSock');
  const ledRtc=document.getElementById('ledRtc'), ledIce=document.getElementById('ledIce');
  const ledStt=document.getElementById('ledStt'), stStt=document.getElementById('stStt');
  const ledAi=document.getElementById('ledAi'), stAi=document.getElementById('stAi');
  const stPc=document.getElementById('stPc'), stIce=document.getElementById('stIce'), stBr=document.getElementById('stBr');
  const roomStatus=document.getElementById('roomStatus');

  const camLed=document.getElementById('camLed'), camTxt=document.getElementById('camTxt');
  const screenLed=document.getElementById('screenLed'), screenTxt=document.getElementById('screenTxt');
  const ncLed=document.getElementById('ncLed'), ncTxt=document.getElementById('ncTxt');
  const sttLed=document.getElementById('sttLed'), sttTxt=document.getElementById('sttTxt');
  const aiLed=document.getElementById('aiLed'), aiTxt=document.getElementById('aiTxt');
  const ttsMonLed=document.getElementById('ttsMonLed'), ttsMonTxt=document.getElementById('ttsMonTxt');

  let seen=new Set();
  function esc(x){return String(x||'').replace(/[<>&]/g,s=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[s]));}
  function label(m){
    const r=String((m.role||m.sender||'')).toLowerCase();
    if(r.includes('assistant')||r.includes('ai')) return 'AI';
    if(r.includes('broadcaster_stt')||r==='stt') return 'STT';
    if(r.includes('broadcaster')) return 'Broadcaster';
    if(r.includes('viewer')||r.includes('watch')) return 'Viewer';
    if(r==='system') return 'System';
    return 'User';
  }
  function addMsg(m){
    const id=m.message_id||`${m.sender}|${m.role}|${m.ts}|${m.text}`;
    if(seen.has(id)) return; seen.add(id);
    if(seen.size>1200){ const a=[...seen].slice(-400); seen=new Set(a); }
    const d=document.createElement('div');
    const isStt = label(m)==='STT';
    d.className='entry'+(isStt?' stt':'');
    d.innerHTML=`<b>[${label(m)}]</b> <small>${new Date(m.ts||Date.now()).toLocaleTimeString()}</small><div>${esc(m.text||'')}</div>`;
    chatEl.appendChild(d); chatEl.scrollTop=chatEl.scrollHeight;
    if(label(m)==='AI') lastAiText = String(m.text||'').trim();
  }

  function setLed(el,on,blink=false){
    el.className = 'led ' + (on ? ('g'+(blink?' blink':'')) : 'r');
  }

  async function getIceServers(){
    try{
      const r=await fetch(cfg.iceConfigUrl || '/webrtc/ice-config',{cache:'no-store'});
      if(!r.ok) throw new Error('bad status '+r.status);
      const j=await r.json();
      if(j && j.iceServers && Array.isArray(j.iceServers)) return j.iceServers;
    }catch(e){
      console.warn('[broadcast] ice-config fetch failed, STUN-only', e);
    }
    return [{urls:'stun:stun.l.google.com:19302'}];
  }

  // ---------------- Media state ----------------
  let micStream=null, camStream=null, screenStream=null, composedStream=null;
  let camDevices=[], camIndex=-1;
  let noiseCancel=false;

  function stopStream(s){
    try{ if(s) s.getTracks().forEach(t=>t.stop()); }catch(e){}
  }

  async function refreshCameras(){
    const devs=await navigator.mediaDevices.enumerateDevices();
    camDevices = devs.filter(d=>d.kind==='videoinput');
  }

  function cameraLabel(dev, idx){
    const name = (dev && dev.label) ? dev.label : ('camera '+(idx+1));
    const lower = name.toLowerCase();
    if(lower.includes('back') || lower.includes('rear')) return name + ' (rear)';
    if(lower.includes('front')) return name + ' (front)';
    return name;
  }

  // Ask for MIC first (more reliable on mobile); then camera when needed.
  async function ensureMic(){
    if(micStream) return micStream;
    const constraints={
      audio:{
        echoCancellation: noiseCancel,
        noiseSuppression: noiseCancel,
        autoGainControl: noiseCancel,
        channelCount: 1
      },
      video:false
    };
    micStream = await navigator.mediaDevices.getUserMedia(constraints);
    return micStream;
  }

  async function startCameraByIndex(idx){
    // If we don't have mic permission yet, get it first to unlock labels + user trust prompt.
    try{ await ensureMic(); }catch(e){
      camTxt.textContent='CAM: permission needed';
      setLed(camLed,false);
      addMsg({sender:'system',role:'system',text:'[camera] permission needed — allow Microphone first, then click CAM again.',ts:Date.now()});
      throw e;
    }

    await refreshCameras();

    if(camDevices.length===0){
      camIndex=-1;
      camTxt.textContent='CAM: none';
      setLed(camLed,false);
      stopStream(camStream); camStream=null;
      return;
    }

    if(idx < 0){
      camIndex=-1;
      camTxt.textContent='CAM: off';
      setLed(camLed,false);
      stopStream(camStream); camStream=null;
      return;
    }

    idx = Math.max(0, Math.min(idx, camDevices.length-1));
    const dev = camDevices[idx];

    let s=null;
    try{
      s = await navigator.mediaDevices.getUserMedia({video:{deviceId:{ideal:dev.deviceId}}, audio:false});
    }catch(e1){
      s = await navigator.mediaDevices.getUserMedia({video:{deviceId:{exact:dev.deviceId}}, audio:false});
    }

    stopStream(camStream);
    camStream=s;
    camIndex=idx;

    camTxt.textContent='CAM: '+cameraLabel(dev, idx);
    setLed(camLed,true,true);
  }

  async function cycleCamera(){
    await refreshCameras();
    if(camDevices.length===0){
      await startCameraByIndex(-1);
      return;
    }
    // cycle: cam0 -> cam1 -> ... -> off -> cam0
    let next;
    if(camIndex === -1) next = 0;
    else {
      next = camIndex + 1;
      if(next >= camDevices.length) next = -1;
    }
    await startCameraByIndex(next);
  }

  // ---------------- Screen compositor (screen + PiP cam + mic) ----------------
  const _screenVid=document.createElement('video'); _screenVid.playsInline=true; _screenVid.muted=true;
  const _camVid=document.createElement('video'); _camVid.playsInline=true; _camVid.muted=true;
  const _canvas=document.createElement('canvas');
  const _ctx=_canvas.getContext('2d');
  let _raf=null;

  function stopCompositor(){
    if(_raf){ cancelAnimationFrame(_raf); _raf=null; }
    composedStream=null;
    try{ _screenVid.srcObject=null; }catch(e){}
    try{ _camVid.srcObject=null; }catch(e){}
  }

  async function stopScreen(){
    stopStream(screenStream); screenStream=null;
    stopCompositor();
    screenTxt.textContent='SCREEN: off';
    setLed(screenLed,false);
  }

  async function startScreenWithPiP(){
    screenStream = await navigator.mediaDevices.getDisplayMedia({video:true, audio:false});
    _screenVid.srcObject=screenStream;
    await _screenVid.play().catch(()=>{});

    await refreshCameras();
    if(!camStream && camDevices.length>0){
      // Best effort PiP cam: if user denies cam, screen share still works.
      try{ await startCameraByIndex(0); }catch(e){}
    }
    if(camStream){
      _camVid.srcObject=camStream;
      await _camVid.play().catch(()=>{});
    }

    const st = screenStream.getVideoTracks()[0];
    const sset = st.getSettings ? st.getSettings() : {};
    _canvas.width = sset.width || 1280;
    _canvas.height = sset.height || 720;

    const pipPad = 16;
    function draw(){
      try{
        _ctx.drawImage(_screenVid,0,0,_canvas.width,_canvas.height);
        if(camStream && _camVid.readyState >= 2){
          const pipW=Math.round(_canvas.width*0.22);
          const pipH=Math.round(pipW*0.75);
          const x=_canvas.width - pipW - pipPad;
          const y=_canvas.height - pipH - pipPad;
          _ctx.fillStyle='rgba(0,0,0,0.35)';
          _ctx.fillRect(x-6,y-6,pipW+12,pipH+12);
          _ctx.drawImage(_camVid,x,y,pipW,pipH);
        }
      }catch(e){}
      _raf=requestAnimationFrame(draw);
    }
    draw();

    const fps=30;
    const canvasStream=_canvas.captureStream(fps);

    const ms = new MediaStream();
    const v = canvasStream.getVideoTracks()[0];
    if(v) ms.addTrack(v);

    await ensureMic();
    const a = micStream && micStream.getAudioTracks()[0];
    if(a) ms.addTrack(a);

    composedStream=ms;

    st.addEventListener('ended', async()=>{ await stopScreen(); await syncTracks(); });

    screenTxt.textContent='SCREEN: on (PiP)';
    setLed(screenLed,true,true);
  }

  // ---------------- WebRTC ----------------
  let pc=null;
  let makingOffer=false;
  let lastAiText='';
  let sttEnabled=false;
  let aiActive=false;
  let ttsMonitor=false;
  let sttRec=null;

  // Keep explicit transceivers so replaceTrack is always safe.
  let txVideo=null;
  let txAudio=null;

  async function ensurePc(){
    if(pc) return;
    const iceServers = await getIceServers();
    pc=new RTCPeerConnection({iceServers});

    txVideo = pc.addTransceiver('video',{direction:'sendrecv'});
    txAudio = pc.addTransceiver('audio',{direction:'sendrecv'});

    pc.onicecandidate=e=>{ if(e.candidate){ socket.emit('webrtc_ice',{candidate:e.candidate}); } };
    pc.onconnectionstatechange=()=>{
      stPc.textContent=pc.connectionState;
      setLed(ledRtc, pc.connectionState==='connected', pc.connectionState==='connecting');
    };
    pc.oniceconnectionstatechange=()=>{
      stIce.textContent=pc.iceConnectionState;
      setLed(ledIce, pc.iceConnectionState==='connected' || pc.iceConnectionState==='completed', pc.iceConnectionState==='checking');
    };
  }

   async function negotiate(){
    if(!pc || makingOffer) return;

    // If not stable, wait a beat; avoids errors during async churn
    if(pc.signalingState !== 'stable'){
      await new Promise(r=>setTimeout(r,150));
      if(pc.signalingState !== 'stable') return;
    }

    makingOffer = true;
    try{
      // IMPORTANT: always pass an explicit offer into setLocalDescription (Firefox-safe)
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      socket.emit('webrtc_offer', {
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type
      });
    } catch(e){
      console.error('[negotiate] failed', e);
    } finally{
      makingOffer = false;
    }
  }
  
  async function syncTracks(){
    await ensurePc();
    await ensureMic();

    const useStream = composedStream
      ? composedStream
      : new MediaStream([
          ...(camStream ? camStream.getVideoTracks() : []),
          ...(micStream ? micStream.getAudioTracks() : []),
        ]);

    preview.srcObject = composedStream ? composedStream : (camStream || null);

    const vTrack = useStream.getVideoTracks()[0] || null;
    const aTrack = useStream.getAudioTracks()[0] || null;

    // Use the transceivers' senders (no duplicates, no “already set on sender”)
    if(txVideo && txVideo.sender) await txVideo.sender.replaceTrack(vTrack);
    if(txAudio && txAudio.sender) await txAudio.sender.replaceTrack(aTrack);

    await negotiate();
  }

  // bitrate (video+audio)
  let _lastBytes=0,_lastTs=performance.now();
  setInterval(async()=>{
    if(!pc) return;
    try{
      const stats=await pc.getStats();
      let bytes=0;
      stats.forEach(r=>{ if(r.type==='outbound-rtp'){ bytes += (r.bytesSent||0); }});
      const now=performance.now(); const dt=Math.max(0.001,(now-_lastTs)/1000);
      const kbps=((bytes-_lastBytes)*8/1000)/dt;
      _lastBytes=bytes; _lastTs=now;
      stBr.textContent = `${Math.max(0,Math.round(kbps))} kbps`;
    }catch(e){}
  },2000);

  // ---------------- STT chunking ----------------
  async function startStt(){
    if(sttRec) return;
    await ensureMic();
    const mime=MediaRecorder.isTypeSupported('audio/webm;codecs=opus')?'audio/webm;codecs=opus':'';
    sttRec=new MediaRecorder(micStream, mime?{mimeType:mime}:undefined);
    sttRec.ondataavailable=async (ev)=>{
      if(!ev.data || !ev.data.size) return;
      const ab=await ev.data.arrayBuffer();
      let bin=''; const b=new Uint8Array(ab);
      for(let i=0;i<b.length;i+=0x8000){ bin+=String.fromCharCode.apply(null,b.subarray(i,i+0x8000)); }
      socket.emit('stt_chunk',{b64:btoa(bin),mime:ev.data.type||'audio/webm',ts:Date.now()});
    };
    sttRec.start(500);
    sttEnabled=true;
    sttTxt.textContent='STT: on';
    stStt.textContent='on';
    setLed(sttLed,true,true);
    setLed(ledStt,true,true);
    socket.emit('set_room_settings',{stt_enabled:true});
  }
  function stopStt(){
    try{ if(sttRec) sttRec.stop(); }catch(e){}
    sttRec=null;
    sttEnabled=false;
    sttTxt.textContent='STT: off';
    stStt.textContent='off';
    setLed(sttLed,false);
    setLed(ledStt,false);
    socket.emit('set_room_settings',{stt_enabled:false});
  }

  // ---------------- TTS playback monitor ----------------
  function setTtsMon(on){
    ttsMonitor=!!on;
    ttsMonTxt.textContent = 'Hear AI voice: ' + (ttsMonitor?'on':'off');
    setLed(ttsMonLed, ttsMonitor, ttsMonitor);
    socket.emit('set_room_settings',{tts_enabled: ttsMonitor});
  }
  function playUrl(url){
    try{ const a=new Audio(url); a.volume=1.0; a.play().catch(()=>{}); }catch(e){}
  }

  // ---------------- AI toggle ----------------
  function setAiActive(on){
    aiActive=!!on;
    aiTxt.textContent = 'AI: ' + (aiActive?'active':'idle');
    stAi.textContent = aiActive?'active':'idle';
    setLed(aiLed, aiActive, aiActive);
    setLed(ledAi, aiActive, aiActive);
    socket.emit('set_room_settings',{ai_enabled: aiActive});
    socket.emit('ai_settings_set',{ai_power: aiActive, ai_mode: aiActive?'active':'idle'});
  }

  function setNoiseCancel(on){
    noiseCancel=!!on;
    ncTxt.textContent = 'NoiseCancel: ' + (noiseCancel?'on':'off');
    setLed(ncLed, noiseCancel, noiseCancel);

    if(micStream){ stopStream(micStream); micStream=null; }
    ensureMic().then(()=>syncTracks()).catch(console.error);
  }

  // ---------------- Socket events ----------------
  socket.on('connect', async()=>{
    dbg('socket connected', room);
    stSock.textContent='connected';
    setLed(ledSock,true,true);

    // defaults
    setNoiseCancel(false);
    setAiActive(true);
    setTtsMon(false);

    // IMPORTANT ORDER (fixes watch “waiting for broadcaster”):
    // 1) get mic (permission prompt)
    // 2) build pc
    // 3) try camera
    // 4) attach tracks + THEN offer
    try{
      await ensureMic();
    }catch(e){
      addMsg({sender:'system',role:'system',text:'[mic] permission needed — allow Microphone for this site, then reload.',ts:Date.now()});
      return;
    }

    await ensurePc();

    // Best-effort auto camera on load (some mobile requires click; if denied, we keep mic-only)
    try{
      await refreshCameras();
      if(camDevices.length>0){
        await startCameraByIndex(0);
      }else{
        camTxt.textContent='CAM: none';
        setLed(camLed,false);
      }
    }catch(e){
      camTxt.textContent='CAM: permission needed';
      setLed(camLed,false);
      addMsg({sender:'system',role:'system',text:'[camera] permission needed — click CAM to retry.',ts:Date.now()});
    }

    // This sends the first offer AFTER tracks exist => server receives on_track => watch works
    await syncTracks();

    // STT defaults ON
    await startStt().catch(()=>{});
  });

  socket.on('disconnect', ()=>{
    dbg('socket disconnected');
    stSock.textContent='disconnected';
    setLed(ledSock,false);
  });

  socket.on('webrtc_answer', async(ans)=>{
    try{ await ensurePc(); await pc.setRemoteDescription(ans); }catch(e){ console.error(e); }
  });
  socket.on('webrtc_ice_server', async(p)=>{
    try{ if(pc && p && p.candidate) await pc.addIceCandidate(p.candidate || p); }catch(e){}
  });

  socket.on('chat_message', addMsg);
  socket.on('stt_text', (p)=> addMsg({sender:'stt',role:'broadcaster_stt',text:p.text,ts:p.ts||Date.now()}));
  socket.on('room_status', (p)=>{
    if(!p) return;
    roomStatus.textContent = `viewers:${p.viewer_count||0} broadcaster:${p.broadcaster_present?'yes':'no'}`;
  });
  socket.on('ai_tts_audio', (p)=>{
    if(p && p.url && ttsMonitor){
      playUrl(p.url);
    }
  });

  // ---------------- UI actions ----------------
  document.getElementById('sendBtn').onclick=()=>{
    const t=input.value.trim();
    if(!t) return;
    socket.emit('chat_message',{text:t,ts:Date.now()});
    input.value='';
  };

  // CAM cycles cameras (and includes OFF as last step)
  document.getElementById('camBtn').onclick=async()=>{
    try{
      await cycleCamera();           // user gesture -> permission prompt if needed
      if(screenStream){
        // PiP changes too
        try{ _camVid.srcObject = camStream; await _camVid.play().catch(()=>{}); }catch(e){}
      }
      await syncTracks();            // renegotiate after change
    }catch(e){
      addMsg({sender:'system',role:'system',text:'[camera] failed to switch (permission denied?)',ts:Date.now()});
    }
  };

  // SCREEN toggles compositor (screen + PiP cam + mic)
  document.getElementById('screenBtn').onclick=async()=>{
    if(screenStream){
      await stopScreen();
      await syncTracks();
      return;
    }
    try{
      await startScreenWithPiP();
      await syncTracks();
    }catch(e){
      console.error('[screen] start failed', e);
      addMsg({sender:'system',role:'system',text:'[screen] failed — browser blocked screen capture or user canceled.',ts:Date.now()});
      await stopScreen();
      await syncTracks();
    }
  };

  document.getElementById('aiBtn').onclick=()=> setAiActive(!aiActive);
  document.getElementById('ncBtn').onclick=()=> setNoiseCancel(!noiseCancel);
  document.getElementById('sttBtn').onclick=()=> (sttEnabled?stopStt():startStt());
  document.getElementById('ttsMonBtn').onclick=()=> setTtsMon(!ttsMonitor);
  document.getElementById('speakBtn').onclick=()=>{ if(lastAiText) socket.emit('speak_last_ai',{text:lastAiText}); };

  document.getElementById('attachBtn').onclick=()=>document.getElementById('file').click();
  document.getElementById('file').onchange=async (e)=>{
    const f=e.target.files[0]; if(!f) return;
    const ab=await f.arrayBuffer(); const b=new Uint8Array(ab);
    let bin=''; for(let i=0;i<b.length;i+=0x8000){ bin+=String.fromCharCode.apply(null,b.subarray(i,i+0x8000)); }
    socket.emit('upload_file',{name:f.name,mime:f.type,content_base64:btoa(bin),text:'uploaded '+f.name});
  };

  document.getElementById('webBtn').onclick=()=>{
    const q=input.value.trim();
    if(q) socket.emit('web_search',{query:q});
  };

  window.addEventListener('beforeunload', ()=>{
    try{ if(sttRec) sttRec.stop(); }catch(e){}
    stopStream(camStream);
    stopStream(screenStream);
    stopStream(micStream);
    stopCompositor();
    try{ if(pc) pc.close(); }catch(e){}
  });
      dbg('ready');
    }
  };

  window.BroadcastApp = BroadcastApp;
  window.addEventListener('DOMContentLoaded', () => BroadcastApp.init(), { once: true });
})();
