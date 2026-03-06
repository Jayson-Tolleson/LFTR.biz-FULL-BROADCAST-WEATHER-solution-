#!/usr/bin/env bash
set -euo pipefail
# ------------------ Defaults (override with flags) ------------------
APP_USER="${APP_USER:-${SUDO_USER:-${USER:-jayson_tolleson}}}"
APP_HOME="/home/${APP_USER}"
APP_DIR="${APP_HOME}/broadcast"
ZIPNAME="${APP_HOME}/broadcast_app_package.zip"
DOMAIN="lftr.biz"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"

HYPERCORN_BIND="127.0.0.1:8000"
PYTHON_BIN="/usr/bin/python3"
SYSTEMD_UNIT_NAME="broadcast.service"
NGINX_ENABLED=true
SYSTEMD_ENABLED=true
FORCE=false
# Allow env override (e.g. sudo ASSUME_YES=true bash install.sh)
ASSUME_YES="${ASSUME_YES:-false}"
STRICT_AUTH=false
GCLOUD_LOGIN="${GCLOUD_LOGIN:-false}"
INTERACTIVE=true
ORIGINAL_HAS_TTY=false
if [[ -t 0 && -t 1 ]]; then
  ORIGINAL_HAS_TTY=true
fi
if [[ "$ORIGINAL_HAS_TTY" == "true" ]]; then
  exec 3>/dev/tty
else
  exec 3>/dev/null
fi

# ------------------ helpers ------------------
INSTALL_LOG="${INSTALL_LOG:-/tmp/broadcast_install_$(date +%Y%m%d_%H%M%S).log}"
mkdir -p "$(dirname "$INSTALL_LOG")"
touch "$INSTALL_LOG"
exec > >(tee >(sed -E "s/\x1B\[[0-9;]*[A-Za-z]//g" >> "$INSTALL_LOG")) 2>&1

# ------------------ theme ------------------
BRIGHT_BOLD='\033[1m'
BRIGHT_CYAN='\033[1;96m'
BRIGHT_GREEN='\033[1;92m'
BRIGHT_YELLOW='\033[1;93m'
BRIGHT_RED='\033[1;91m'
BRIGHT_MAGENTA='\033[1;95m'
RESET='\033[0m'

if [[ -n "${NO_COLOR:-}" ]] || [[ "$ORIGINAL_HAS_TTY" != "true" ]]; then
  BRIGHT_BOLD=''
  BRIGHT_CYAN=''
  BRIGHT_GREEN=''
  BRIGHT_YELLOW=''
  BRIGHT_RED=''
  BRIGHT_MAGENTA=''
  RESET=''
fi

banner() {
  printf "\n%s╔══════════════════════════════════════════════════════════════╗%s\n" "$BRIGHT_MAGENTA" "$RESET"
  printf "%s║                  B R O A D C A S T   M A C H I N E         ║%s\n" "$BRIGHT_CYAN" "$RESET"
  printf "%s║                   N E O N   C O N S O L E                  ║%s\n" "$BRIGHT_MAGENTA" "$RESET"
  printf "%s╚══════════════════════════════════════════════════════════════╝%s\n" "$BRIGHT_MAGENTA" "$RESET"
}

section() {
  local name="$1"
  printf "\n%s━━━ %s%s%s ━━━%s\n" "$BRIGHT_MAGENTA" "$BRIGHT_BOLD" "$name" "$BRIGHT_MAGENTA" "$RESET"
}

log()  { printf "\n%s⟦ INSTALL ⟧%s %s\n" "$BRIGHT_CYAN" "$RESET" "$*"; }
ok()   { printf "\n%s⟦ OK ⟧%s %s\n" "$BRIGHT_GREEN" "$RESET" "$*"; }
warn() { printf "\n%s⟦ WARN ⟧%s %s\n" "$BRIGHT_YELLOW" "$RESET" "$*"; }
err()  { printf "\n%s⟦ FAIL ⟧%s %s\n" "$BRIGHT_RED" "$RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

done_box() {
  printf "\n%s╔══════════════════════════════════════════════════════════════╗%s\n" "$BRIGHT_GREEN" "$RESET"
  printf "%s║ Broadcast Machine has Finished!!!                           ║%s\n" "$BRIGHT_GREEN" "$RESET"
  printf "%s║ Please visit the URLs:                                     ║%s\n" "$BRIGHT_CYAN" "$RESET"
  printf "%s║   https://%s/broadcast                                      ║%s\n" "$BRIGHT_CYAN" "$DOMAIN" "$RESET"
  printf "%s║   https://%s/watch                                          ║%s\n" "$BRIGHT_CYAN" "$DOMAIN" "$RESET"
  printf "%s║ Quick verify:                                               ║%s\n" "$BRIGHT_MAGENTA" "$RESET"
  printf "%s║   sudo systemctl status broadcast --no-pager -l             ║%s\n" "$BRIGHT_MAGENTA" "$RESET"
  printf "%s║   sudo journalctl -u broadcast -n 120 --no-pager -l         ║%s\n" "$BRIGHT_MAGENTA" "$RESET"
  printf "%s╚══════════════════════════════════════════════════════════════╝%s\n" "$BRIGHT_GREEN" "$RESET"
}

attempt() {
  local name="$1"
  shift
  log "START: ${name}"

  local -a cmd=("$@")
  local -a run_cmd=()
  local -a apt_opts=("-y" "-o" "Dpkg::Use-Pty=0" "-o" "Dpkg::Progress-Fancy=0")
  local is_apt=false
  local apt_idx=-1
  local i

  for i in "${!cmd[@]}"; do
    if [[ "${cmd[$i]}" == "apt-get" ]]; then
      is_apt=true
      apt_idx="$i"
      break
    fi
  done

  if [[ "$is_apt" == true ]]; then
    export DEBIAN_FRONTEND=noninteractive
    if (( apt_idx + 1 < ${#cmd[@]} )); then
      local subcmd="${cmd[$((apt_idx + 1))]}"
      if [[ "$subcmd" == "install" || "$subcmd" == "update" || "$subcmd" == "upgrade" || "$subcmd" == "dist-upgrade" ]]; then
        cmd=("${cmd[@]:0:$((apt_idx + 2))}" "${apt_opts[@]}" "${cmd[@]:$((apt_idx + 2))}")
      fi
    fi
  fi

  if command -v stdbuf >/dev/null 2>&1; then
    run_cmd=(stdbuf -oL -eL "${cmd[@]}")
  else
    run_cmd=("${cmd[@]}")
  fi

  set +e
  "${run_cmd[@]}"
  local rc=$?
  set -e

  if [ $rc -ne 0 ]; then
    err "FAILED (continuing): ${name}"
  else
    ok "OK: ${name}"
  fi
  return 0
}


# ------------------ progress UI (disabled; plain text only) ------------------
GAUGE_FD=""
GAUGE_PID=""

gauge_update() { return 0; }
gauge_stop() { return 0; }
gauge_end() { return 0; }


# ------------------ plain-text UI ------------------
has_tty() {
  [[ "${ORIGINAL_HAS_TTY}" == "true" ]]
}

is_interactive() {
  [[ "${INTERACTIVE:-true}" == "true" ]] && [[ "$ORIGINAL_HAS_TTY" == "true" ]] && [[ "${TERM:-}" != "dumb" ]]
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# ------------------ environment detection (GCP + local) ------------------
MD_BASE="http://169.254.169.254/computeMetadata/v1"
md_get() {
  local path="$1"
  curl -fsS -H "Metadata-Flavor: Google" "${MD_BASE}${path}" 2>/dev/null || true
}
is_gcp_vm() {
  curl -fsS -H "Metadata-Flavor: Google" "${MD_BASE}/instance/id" >/dev/null 2>&1
}
zone_to_region() {
  # us-west1-a -> us-west1
  local z="$1"
  echo "${z%-*}"
}
detect_environment_defaults() {
  DETECTED_OS="$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}" || echo unknown)"
  DETECTED_USER="${SUDO_USER:-${USER:-}}"
  if [[ -z "${DETECTED_USER}" ]]; then
    DETECTED_USER="$(logname 2>/dev/null || true)"
  fi
  DETECTED_USER="${DETECTED_USER:-$APP_USER}"
  DETECTED_HOSTNAME="$(hostname -s 2>/dev/null || echo unknown)"

  DETECTED_GCP="false"
  DETECTED_PROJECT_ID=""
  DETECTED_VM_ZONE=""
  DETECTED_VM_REGION=""
  DETECTED_INSTANCE=""
  DETECTED_MACHINE_TYPE=""
  DETECTED_SA_EMAIL=""

  if is_gcp_vm; then
    DETECTED_GCP="true"
    DETECTED_PROJECT_ID="$(md_get /project/project-id)"
    DETECTED_VM_ZONE="$(md_get /instance/zone | awk -F/ '{print $NF}')"
    DETECTED_VM_REGION="$(zone_to_region "$DETECTED_VM_ZONE")"
    DETECTED_INSTANCE="$(md_get /instance/name)"
    DETECTED_MACHINE_TYPE="$(md_get /instance/machine-type | awk -F/ '{print $NF}')"
    DETECTED_SA_EMAIL="$(md_get /instance/service-accounts/default/email)"
  fi

  # If gcloud exists, use its config only as a fallback (metadata is preferred on GCE)
  if command -v gcloud >/dev/null 2>&1; then
    local gproj gzone
    gproj="$(gcloud config get-value project 2>/dev/null || true)"
    gzone="$(gcloud config get-value compute/zone 2>/dev/null || true)"
    # NOTE: Avoid "[[ ... ]] && ..." under "set -e"; some environments treat the
    # false condition as a hard error and exit early. Use explicit if statements.
    if [[ -z "${DETECTED_PROJECT_ID}" && -n "${gproj}" ]]; then
      DETECTED_PROJECT_ID="$gproj"
    fi
    if [[ -z "${DETECTED_VM_ZONE}" && -n "${gzone}" ]]; then
      DETECTED_VM_ZONE="$gzone"
    fi
    if [[ -z "${DETECTED_VM_REGION}" && -n "${DETECTED_VM_ZONE}" ]]; then
      DETECTED_VM_REGION="$(zone_to_region "$DETECTED_VM_ZONE")"
    fi
  fi
}

UI="plain"
trap 'gauge_stop || true' EXIT

init_ui() {
  UI="plain"
}

# ... [content intentionally truncated in this first half as provided by user] ...

async def _stop_stt_for_room(room_id: str):
    handle = _stt_handles.pop(room_id, None)
    if not handle:
        return
    handle.stop_event.set()
    handle.task.cancel()
    try:
        await handle.task
    except Exception:
        pass
    await _emit_room(room_id, "stt_status", {"room": room_id, "status": "stopped", "ts": int(time.time()*1000)})
async def _schedule_cleanup(room_id: str, sid: str, role: str):
    # Allow transient disconnects
    key = f"{room_id}:{sid}:{role}"
    task = _pending_cleanup.get(key)
    if task:
        task.cancel()

    async def _runner():
        await asyncio.sleep(DISCONNECT_GRACE_SECONDS)
        if role == "broadcaster":
            b = state.broadcasters.get(room_id)
            if b and b.sid == sid and b.pc.connectionState == "disconnected":
                await stop_broadcaster(room_id, sid)
        else:
            pc = state.viewers.get(room_id, {}).get(sid)
            if pc and pc.connectionState == "disconnected":
                await stop_viewer(room_id, sid)

    _pending_cleanup[key] = asyncio.create_task(_runner())


async def start_broadcaster_from_offer(room_id: str, sid: str, sdp: str, sdp_type: str) -> dict:
    """
    Called when /broadcast sends its WebRTC offer.
    Creates a peer connection, sets remote desc, returns answer SDP.
    """
    # If a broadcaster already exists in room, replace it.
    if room_id in state.broadcasters:
        try:
            await stop_broadcaster(room_id, state.broadcasters[room_id].sid)
        except Exception:
            pass

    pc = RTCPeerConnection()
    b = BroadcastSession(room_id=room_id, sid=sid, pc=pc)
    state.broadcasters[room_id] = b

    @pc.on("connectionstatechange")
    async def _():
        st = pc.connectionState
        log.info("broadcaster connectionstate room=%s sid=%s state=%s", room_id, sid, st)
        await _emit_room(room_id, "webrtc_state", {"room": room_id, "role": "broadcaster", "state": st, "ts": int(time.time()*1000)})
        if st == "disconnected":
            await _schedule_cleanup(room_id, sid, "broadcaster")
        if st in ("failed", "closed"):
            await stop_broadcaster(room_id, sid)

    @pc.on("track")
    async def on_track(track):
        log.info("track received room=%s kind=%s sid=%s", room_id, track.kind, sid)
        b.tracks[track.kind] = track
        await _emit_room(room_id, "stream_started", {"room": room_id, "kind": track.kind, "ts": int(time.time()*1000)})
        # Start STT when audio arrives
        if track.kind == "audio":
            await _start_stt_for_room(room_id)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    await _wait_for_ice_gathering_complete(pc)
    await _emit_status(room_id)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def start_viewer_offer(room_id: str, sid: str) -> dict:
    """
    Called when /watch joins. Server creates a peer connection that relays broadcaster tracks.
    Returns offer to the watcher.
    """
    pc = RTCPeerConnection()
    state.viewers.setdefault(room_id, {})[sid] = pc

    @pc.on("connectionstatechange")
    async def _():
        st = pc.connectionState
        log.info("viewer connectionstate room=%s sid=%s state=%s", room_id, sid, st)
        await _emit_room(room_id, "webrtc_state", {"room": room_id, "role": "watch", "state": st, "ts": int(time.time()*1000)})
        if st == "disconnected":
            await _schedule_cleanup(room_id, sid, "viewer")
        if st in ("failed", "closed"):
            await stop_viewer(room_id, sid)

    # Attach current broadcaster tracks if present
    b = state.broadcasters.get(room_id)
    if b:
        for kind, tr in b.tracks.items():
            try:
                pc.addTrack(relay.subscribe(tr))
            except Exception:
                log.exception("failed to add relayed %s track", kind)
    else:
        # No broadcaster yet: watcher will still connect but get no tracks; UI shows waiting.
        pass

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await _wait_for_ice_gathering_complete(pc)
    await _emit_status(room_id)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def set_viewer_answer(room_id: str, sid: str, sdp: str, sdp_type: str):
    pc = state.viewers.get(room_id, {}).get(sid)
    if not pc:
        raise RuntimeError("Viewer peer not found")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))


def parse_ice(payload: dict) -> Optional[RTCIceCandidate]:
    payload = payload or {}
    cand = payload.get("candidate")
    # Some clients send a full RTCIceCandidateInit object in `candidate`
    if isinstance(cand, dict):
        nested = cand
        cand = nested.get("candidate") or ""
        # prefer nested mid/mline if present
        payload = {**payload, **nested}

    if not cand:
        return None

    sdp_mid = payload.get("sdpMid")
    sdp_mline = payload.get("sdpMLineIndex")

    if isinstance(cand, str) and cand.startswith("candidate:"):
        cand = cand[len("candidate:"):]
    c = candidate_from_sdp(str(cand))
    c.sdpMid = sdp_mid
    c.sdpMLineIndex = sdp_mline
    return c


async def add_broadcaster_ice_candidate(room_id: str, sid: str, candidate: Optional[RTCIceCandidate]):
    b = state.broadcasters.get(room_id)
    if not b or b.sid != sid:
        return
    await b.pc.addIceCandidate(candidate)


async def add_viewer_ice_candidate(room_id: str, sid: str, candidate: Optional[RTCIceCandidate]):
    pc = state.viewers.get(room_id, {}).get(sid)
    if not pc:
        return
    await pc.addIceCandidate(candidate)


async def stop_viewer(room_id: str, sid: str):
    pc = state.viewers.get(room_id, {}).pop(sid, None)
    if pc:
        try:
            await pc.close()
        except Exception:
            log.exception("error closing viewer pc")
    await _emit_status(room_id)


async def stop_broadcaster(room_id: str, sid: str):
    b = state.broadcasters.get(room_id)
    if not b or b.sid != sid:
        return
    # stop STT for room
    await _stop_stt_for_room(room_id)

    # close broadcaster pc
    try:
        await b.pc.close()
    except Exception:
        log.exception("error closing broadcaster pc")
    state.broadcasters.pop(room_id, None)

    # close all viewers pcs in room (force reconnect)
    viewers = list(state.viewers.get(room_id, {}).keys())
    for vsid in viewers:
        try:
            await stop_viewer(room_id, vsid)
        except Exception:
            pass

    await _emit_room(room_id, "stream_stopped", {"room": room_id, "ts": int(time.time()*1000)})
    await _emit_status(room_id)
EOF_SERVER_WEBRTC_PY



write_file "server/socket_handlers.py" <<'EOF_SERVER_SOCKET_HANDLERS_PY'
# server/socket_handlers.py
import asyncio
import base64
import logging
import time
from typing import Any, Dict
from urllib.parse import parse_qs

from server import ai, webrtc
from server.state import room_all, room_broadcast, room_watch, state

log = logging.getLogger("server.socket_handlers")

# Per-sid STT buffers
_stt_buffers: Dict[str, bytearray] = {}
_stt_last_emit: Dict[str, float] = {}
_stt_warned: Dict[str, bool] = {}

# Per-sid WebM init segment (EBML header). Needed because MediaRecorder chunks after the first
# often omit the header; ffmpeg then fails with "Invalid data found".
_stt_init: Dict[str, bytes] = {}

def _looks_like_webm_init(b: bytes) -> bool:
    # WebM/Matroska EBML header starts with 0x1A45DFA3
    return len(b) >= 4 and b[0] == 0x1A and b[1] == 0x45 and b[2] == 0xDF and b[3] == 0xA3


def _sid_room_role(sid: str) -> tuple[str, str]:
    return state.sid_meta.get(sid, ("default", "unknown"))


async def _emit_room_status(room_id: str):
    if not state.sio:
        return
    settings = state.get_room_settings(room_id)
    payload = {
        "room": room_id,
        "viewer_count": state.viewer_count(room_id),
        "broadcaster_present": state.broadcaster_present(room_id),
        "ai": {
            "enabled": bool(settings.ai_enabled and state.ai_effective_enabled()),
            "power": state.ai_power,
            "mode": state.ai_mode,
            "fallback": state.ai_fallback,
        },
        "stt_enabled": settings.stt_enabled,
        "tts_enabled": settings.tts_enabled,
        "ts": int(time.time() * 1000),
    }
    await state.sio.emit("room_status", payload, to=room_all(room_id))


async def _emit_sys(room_id: str, text: str):
    if not state.sio:
        return
    await state.sio.emit("chat_message", {"sender": "system", "role": "system", "text": text, "ts": int(time.time()*1000)}, to=room_all(room_id))


def _parse_connect_query(environ: dict) -> tuple[str, str]:
    # socket.io ASGI environ has QUERY_STRING
    qs = environ.get("QUERY_STRING", "") or ""
    q = parse_qs(qs)
    room = (q.get("room", ["default"])[0] or "default").strip()
    role = (q.get("role", ["unknown"])[0] or "unknown").strip()
    # defensive: keep room simple
    room = "".join([c for c in room if c.isalnum() or c in ("-", "_")])[:48] or "default"
    if role not in ("broadcast", "watch"):
        role = "unknown"
    return room, role


async def _ai_reply(room_id: str, prompt: str, source: str = "chat"):
    settings = state.get_room_settings(room_id)
    if not (settings.ai_enabled and state.ai_effective_enabled()):
        return

    # LLM
    text = await ai.ai_chat(prompt, conversation_id=room_id)
    if not text:
        return
    payload = {"sender": "ai", "role": "assistant", "text": text, "ts": int(time.time()*1000)}
    await state.sio.emit("chat_message", payload, to=room_all(room_id))

    # TTS
    if settings.tts_enabled:
        mime, _bytes, url = await ai.synthesize_text(text)
        if url:
            await state.sio.emit("ai_tts_audio", {"room": room_id, "url": url, "mime": mime, "ts": int(time.time()*1000)}, to=room_all(room_id))


def register_socket_handlers(sio):
    @sio.event
    async def connect(sid, environ, auth):
        room_id, role = _parse_connect_query(environ)
        state.sid_meta[sid] = (room_id, role)

        await sio.enter_room(sid, room_all(room_id))
        if role == "broadcast":
            await sio.enter_room(sid, room_broadcast(room_id))
        elif role == "watch":
            await sio.enter_room(sid, room_watch(room_id))

        log.info("[SOCKET] connect sid=%s room=%s role=%s", sid, room_id, role)
        await sio.emit("ai_status", {
            "ai_power": state.ai_power,
            "ai_mode": state.ai_mode,
            "effective_enabled": state.ai_effective_enabled(),
            "fallback": state.ai_fallback,
            "room": room_id,
            "role": role,
            "ts": int(time.time()*1000),
        }, to=sid)

        await _emit_room_status(room_id)

        # If watch joins and no broadcaster, tell them explicitly.
        if role == "watch" and not state.broadcaster_present(room_id):
            await sio.emit("waiting_for_broadcaster", {"room": room_id, "ts": int(time.time()*1000)}, to=sid)

    @sio.event
    async def disconnect(sid):
        room_id, role = _sid_room_role(sid)
        log.info("[SOCKET] disconnect sid=%s room=%s role=%s", sid, room_id, role)

        state.sid_meta.pop(sid, None)
        _stt_buffers.pop(sid, None)
        _stt_last_emit.pop(sid, None)
        _stt_warned.pop(sid, None)

        # If broadcaster disconnects, stop room
        b = state.broadcasters.get(room_id)
        if b and b.sid == sid:
            await webrtc.stop_broadcaster(room_id, sid)
        else:
            await webrtc.stop_viewer(room_id, sid)

        await _emit_room_status(room_id)

    # ---------------- Chat ----------------
    @sio.on("chat_message")
    async def on_chat_message(sid, data):
        room_id, role = _sid_room_role(sid)
        text = (data or {}).get("text", "")
        text = (text or "").strip()
        if not text:
            return

        msg = {"sender": "user" if role != "broadcast" else "broadcaster", "role": "user", "text": text, "ts": int(time.time()*1000)}
        await sio.emit("chat_message", msg, to=room_all(room_id))

        # AI reply (async, do not block)
        settings = state.get_room_settings(room_id)
        if getattr(settings, "ai_power", True) and (getattr(settings, "ai_mode", "active") or "active").lower() == "active":
            asyncio.create_task(_ai_reply(room_id, text, source="chat"))

    @sio.on("speak_last_ai")
    async def speak_last_ai(sid, data):
        room_id, _ = _sid_room_role(sid)
        settings = state.get_room_settings(room_id)
        if not settings.tts_enabled:
            return
        text = (data or {}).get("text", "") or ""
        text = text.strip()
        if not text:
            return
        mime, _bytes, url = await ai.synthesize_text(text)
        if url:
            await sio.emit("ai_tts_audio", {"room": room_id, "url": url, "mime": mime, "ts": int(time.time()*1000)}, to=room_all(room_id))

    # ---------------- Room settings toggles ----------------
    @sio.on("set_room_settings")
    async def set_room_settings(sid, data):
        room_id, role = _sid_room_role(sid)
        settings = state.get_room_settings(room_id)
        if "ai_enabled" in (data or {}):
            settings.ai_enabled = bool(data["ai_enabled"])
        if "tts_enabled" in (data or {}):
            settings.tts_enabled = bool(data["tts_enabled"])
        if "stt_enabled" in (data or {}):
            settings.stt_enabled = bool(data["stt_enabled"])
        await _emit_room_status(room_id)

    # ---------------- STT chunking ----------------
    @sio.on("stt_chunk")
    async def on_stt_chunk(sid, data):
        room_id, role = _sid_room_role(sid)
        # Only accept STT chunks from broadcaster role (best-effort)
        if role != "broadcast":
            return

        settings = state.get_room_settings(room_id)
        if not settings.stt_enabled:
            return

        b64 = (data or {}).get("b64", "") or ""
        if not b64:
            return

        try:
            chunk = base64.b64decode(b64)
        except Exception:
            return

        # Capture init segment (EBML header) when it appears
        if _looks_like_webm_init(chunk):
            _stt_init[sid] = chunk

        buf = _stt_buffers.setdefault(sid, bytearray())
        buf.extend(chunk)

        now = time.time()
        last = _stt_last_emit.get(sid, 0.0)
        if (now - last) < 1.25:
            return
        _stt_last_emit[sid] = now

        window_bytes = bytes(buf)
        buf.clear()

        init = _stt_init.get(sid, b"")
        if not init:
            # Wait for init segment instead of feeding ffmpeg garbage
            if not _stt_warned.get(sid, False):
                _stt_warned[sid] = True
                await _emit_sys(room_id, "[STT] waiting for WebM init segment (refresh broadcast page if this persists)")
            return

        webm_bytes = init + window_bytes

        # Fallback mode produces a mock transcript
        if state.ai_fallback:
            text = "mock transcript"
        else:
            text = await ai.transcribe_webm_opus(webm_bytes)

        text = (text or "").strip()
        if not text:
            return

        # warn once on decode/Google errors
        if text.startswith("[STT decode failed]") or text.startswith("[STT error]") or text.startswith("[STT google error]"):
            if not _stt_warned.get(sid, False):
                _stt_warned[sid] = True
                await _emit_sys(room_id, text)
            return

        # reset warning latch on success
        _stt_warned[sid] = False

        ts = int(time.time()*1000)
        payload = {"text": text, "ts": ts}
        await sio.emit("stt_text", payload, to=room_all(room_id))
        await sio.emit("chat_message", {"sender": "stt", "role": "broadcaster_stt", "text": text, "ts": ts}, to=room_all(room_id))

        # AI should respond to STT too (when active)
        if getattr(settings, "ai_power", True) and (getattr(settings, "ai_mode", "active") or "active").lower() == "active":
            asyncio.create_task(_ai_reply(room_id, text, source="stt"))
    # ---------------- WebRTC signaling ----------------
    @sio.on("webrtc_offer")
    async def on_webrtc_offer(sid, data):
        room_id, role = _sid_room_role(sid)
        if role != "broadcast":
            # still allow if client didn't set role, but enforce single broadcaster semantics.
            pass
        sdp = (data or {}).get("sdp")
        sdp_type = (data or {}).get("type")
        if not sdp or not sdp_type:
            return
        ans = await webrtc.start_broadcaster_from_offer(room_id, sid, sdp, sdp_type)
        await sio.emit("webrtc_answer", ans, to=sid)

    @sio.on("webrtc_ice")
    async def on_webrtc_ice(sid, data):
        room_id, _ = _sid_room_role(sid)
        cand = webrtc.parse_ice(data or {})
        await webrtc.add_broadcaster_ice_candidate(room_id, sid, cand)

    @sio.on("watch_join")
    async def on_watch_join(sid, data):
        room_id, role = _sid_room_role(sid)
        offer = await webrtc.start_viewer_offer(room_id, sid)
        await sio.emit("watch_offer", offer, to=sid)
        if not state.broadcaster_present(room_id):
            await sio.emit("waiting_for_broadcaster", {"room": room_id, "ts": int(time.time()*1000)}, to=sid)

    @sio.on("watch_answer")
    async def on_watch_answer(sid, data):
        room_id, _ = _sid_room_role(sid)
        sdp = (data or {}).get("sdp")
        sdp_type = (data or {}).get("type")
        if not sdp or not sdp_type:
            return
        await webrtc.set_viewer_answer(room_id, sid, sdp, sdp_type)

    @sio.on("watch_ice")
    async def on_watch_ice(sid, data):
        room_id, _ = _sid_room_role(sid)
        cand = webrtc.parse_ice(data or {})
        await webrtc.add_viewer_ice_candidate(room_id, sid, cand)
EOF_SERVER_SOCKET_HANDLERS_PY



write_file "server/routes.py" <<'EOF_SERVER_ROUTES_PY'
import os
from quart import jsonify, request, send_from_directory, Response

from server import ai
from server.youtube_relay import YouTubeRelay
from server.state import ensure_ai_engine


def _normalize_ice_url(raw: str, default_scheme: str = "turn") -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    if val.startswith(("stun:", "turn:", "turns:")):
        return val

    host = val
    port = "3478"
    if ":" in val and "?" not in val:
        maybe_host, maybe_port = val.rsplit(":", 1)
        if maybe_port.isdigit():
            host, port = maybe_host, maybe_port

    if default_scheme == "turns":
        if port == "3478":
            port = "5349"
        return f"turns:{host}:{port}"

    return f"turn:{host}:{port}?transport=udp"


def _build_ice_servers():
    servers = [{"urls": "stun:stun.l.google.com:19302"}]

    turn_user = os.getenv("TURN_USERNAME", "").strip()
    turn_pass = os.getenv("TURN_PASSWORD", "").strip()
    turn_url = _normalize_ice_url(os.getenv("TURN_URL", "").strip(), default_scheme="turn")
    turn_urls = [
        _normalize_ice_url(u.strip(), default_scheme="turn")
        for u in os.getenv("TURN_URLS", "").split(",")
        if u.strip()
    ]
    turn_urls = [u for u in turn_urls if u]
    turns_url = _normalize_ice_url(os.getenv("TURNS_URL", "").strip(), default_scheme="turns")

    dynamic_domain = os.getenv("DOMAIN", "").strip()
    dynamic_public_ip = os.getenv("PUBLIC_IP", "").strip()

    if turn_url:
        turn_urls.append(turn_url)
    if turns_url:
        turn_urls.append(turns_url)

    if not turn_urls and dynamic_public_ip:
        turn_urls.extend([
            f"turn:{dynamic_public_ip}:3478?transport=udp",
            f"turn:{dynamic_public_ip}:3478?transport=tcp",
        ])
    if not turn_urls and dynamic_domain:
        turn_urls.extend([
            f"turn:{dynamic_domain}:3478?transport=udp",
            f"turn:{dynamic_domain}:3478?transport=tcp",
            f"turns:{dynamic_domain}:5349",
        ])

    if turn_urls and turn_user and turn_pass:
        servers.append({
            "urls": turn_urls,
            "username": turn_user,
            "credential": turn_pass,
        })

    return servers


def register_routes(app):
    @app.get("/")
    async def index():
        return await send_from_directory(app.static_folder, "watch.html")

    @app.get("/broadcast")
    async def broadcast():
        return await send_from_directory(app.static_folder, "broadcast.html")

    @app.get("/watch")
    async def watch():
        return await send_from_directory(app.static_folder, "watch.html")

    @app.get("/status-dashboard")
    async def status_dashboard():
        return await send_from_directory(app.static_folder, "status_dashboard.html")

    @app.get("/webrtc/ice-config")
    async def webrtc_ice_config():
        return jsonify({"iceServers": _build_ice_servers()})

    @app.post("/ai/chat")
    async def ai_chat_route():
        data = await request.get_json(force=True)
        text = (data or {}).get("text", "")
        conv = (data or {}).get("conversation_id")
        sid = conv or "http"
        result = await ensure_ai_engine().process_chat(sio=None, sid=sid, sender="http", text=text, message_id=None)
        return jsonify({"reply": (result.text if result else ""), "conversation_id": conv, "command": (result.command if result else None)})

    @app.post("/ai/tts")
    async def ai_tts_route():
        data = await request.get_json(force=True)
        text = (data or {}).get("text", "")
        mime, audio = await ai.synthesize_text(text)
        return Response(audio, content_type=mime)

    @app.post("/ai/websearch")
    async def ai_websearch():
        data = await request.get_json(force=True)
        q = (data or {}).get("query", "")
        return jsonify({"query": q, "results": [{"title": "Mock result", "url": "https://example.com", "snippet": f"No API configured for: {q}"}]})
EOF_SERVER_ROUTES_PY




write_file "static/broadcast.html" <<'EOF_STATIC_BROADCAST_HTML'
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Broadcast</title>
<link rel="stylesheet" href="/static/css.css">
<script src="/static/socket.io.js"></script>
<style>
.app{display:grid;grid-template-rows:auto 1fr;height:100vh;background:#0f1320;color:#e8eefc}
.hdr{display:flex;gap:12px;padding:10px 14px;border-bottom:1px solid #263047;font-size:13px;white-space:nowrap;overflow:auto}
.main{display:grid;grid-template-columns:1fr 360px;gap:12px;padding:12px;min-height:0}
.videoWrap{background:#000;border:1px solid #2a3550;border-radius:12px;overflow:hidden;min-height:320px;position:relative}
video{width:100%;height:100%;object-fit:contain}
.badges{display:flex;gap:8px;flex-wrap:wrap}
.badge{display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid #334161;border-radius:999px;background:#121a2c}
.controls{display:flex;gap:8px;padding:8px;margin-top:8px;flex-wrap:wrap}
.chat{border:1px solid #2a3550;border-radius:12px;display:flex;flex-direction:column;min-height:0;background:#121a2c}
.chatHead{padding:10px;border-bottom:1px solid #27324a;font-size:13px;display:flex;align-items:center;justify-content:space-between}
.chatList{flex:1;overflow:auto;padding:10px}
.entry{padding:8px;border:1px solid #2c3958;border-radius:10px;margin-bottom:8px;background:#0d1423}
.entry.stt{border-color:#60a5fa}
.row{display:flex;gap:6px;padding:8px;border-top:1px solid #27324a;flex-wrap:wrap}
.btn{padding:8px 10px;border-radius:999px;border:1px solid #334161;background:#1a2238;color:#e8efff;cursor:pointer;display:flex;align-items:center;gap:8px}
.btn.small{padding:6px 10px}
.input{display:flex;gap:6px;padding:8px;border-top:1px solid #27324a}
textarea{flex:1;min-height:58px;background:#0b1220;color:#eaf1ff;border:1px solid #334161;border-radius:8px;padding:8px}
.led{width:10px;height:10px;border-radius:999px;display:inline-block}.r{background:#ef4444}.g{background:#22c55e}.b{background:#3b82f6}.blink{animation:bl 1s linear infinite}@keyframes bl{50%{opacity:.25}}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace}
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div class="badges">
      <span class="badge"><span id="ledSock" class="led r"></span><span class="mono">SOCKET</span> <span id="stSock">…</span></span>
      <span class="badge"><span id="ledRtc" class="led r"></span><span class="mono">WEBRTC</span> <span id="stPc">new</span></span>
      <span class="badge"><span id="ledIce" class="led r"></span><span class="mono">ICE</span> <span id="stIce">new</span></span>
      <span class="badge"><span id="ledStt" class="led r"></span><span class="mono">STT</span> <span id="stStt">off</span></span>
      <span class="badge"><span id="ledAi" class="led r"></span><span class="mono">AI</span> <span id="stAi">idle</span></span>
      <span class="badge"><span class="mono">Bitrate</span> <span id="stBr">0 kbps</span></span>
      <span class="badge"><span class="mono">Room</span> <span id="stRoom">default</span></span>
    </div>
  </div>

  <div class="main">
    <div>
      <div class="videoWrap">
        <video id="preview" autoplay playsinline muted></video>
      </div>

      <div class="controls">
        <button id="camBtn" class="btn"><span id="camLed" class="led r"></span><span id="camTxt">CAM: off</span></button>
        <button id="screenBtn" class="btn"><span id="screenLed" class="led r"></span><span id="screenTxt">SCREEN: off</span></button>
        <button id="ncBtn" class="btn"><span id="ncLed" class="led r"></span><span id="ncTxt">NoiseCancel: off</span></button>
        <button id="sttBtn" class="btn"><span id="sttLed" class="led r"></span><span id="sttTxt">STT: off</span></button>
        <button id="aiBtn" class="btn"><span id="aiLed" class="led r"></span><span id="aiTxt">AI: idle</span></button>
        <button id="ttsMonBtn" class="btn"><span id="ttsMonLed" class="led r"></span><span id="ttsMonTxt">Hear AI voice: off</span></button>
        <button id="speakBtn" class="btn"><span class="led b"></span><span>Speak last AI</span></button>
      </div>

      <div style="padding:0 8px 8px 8px;font-size:12px;opacity:.8">
        Tip: If AI voice leaks into STT, keep “Hear AI voice” off and/or use headphones. NoiseCancel uses browser echoCancellation/noiseSuppression.
      </div>
    </div>

    <div class="chat">
      <div class="chatHead">
        <span>Chat</span>
        <span id="roomStatus" style="font-size:12px;opacity:.8"></span>
      </div>
      <div id="chat" class="chatList"></div>
      <div class="row">
        <button id="attachBtn" class="btn small">Attach</button>
        <button id="webBtn" class="btn small">WebSearch</button>
        <input id="file" type="file" hidden>
      </div>
      <div class="input">
        <textarea id="chatInput" placeholder="Message"></textarea>
        <button id="sendBtn" class="btn">Send</button>
      </div>
    </div>
  </div>
</div>

<script>
window.BROADCAST_CONFIG = {
  socketPath: '/socket.io',
  iceConfigUrl: '/webrtc/ice-config',
  aiChatUrl: '/ai/chat',
  aiTtsUrl: '/ai/tts',
  aiWebSearchUrl: '/ai/websearch',
  role: 'broadcast',
  debug: true
};
</script>
<script src="/static/js/broadcast.js"></script>
</body>
</html>

EOF_STATIC_BROADCAST_HTML


write_file "static/js/broadcast.js" <<'EOF_STATIC_JS_BROADCAST_JS'
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
EOF_STATIC_JS_BROADCAST_JS


write_file "static/watch.html" <<'EOF_STATIC_WATCH_HTML'
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Watch</title>
<link rel="stylesheet" href="/static/css.css">
<script src="/static/socket.io.js"></script>
<style>
.app{display:grid;grid-template-rows:auto 1fr;height:100vh;background:#0f1320;color:#e8eefc}
.hdr{display:flex;gap:12px;padding:10px 14px;border-bottom:1px solid #263047;font-size:13px;white-space:nowrap;overflow:auto}
.main{display:grid;grid-template-columns:1fr 360px;gap:12px;padding:12px;min-height:0}
.videoWrap{background:#000;border:1px solid #2a3550;border-radius:12px;overflow:hidden;min-height:300px}
video{width:100%;height:100%;object-fit:contain}
.chat{border:1px solid #2a3550;border-radius:12px;display:flex;flex-direction:column;min-height:0;background:#121a2c}
.chatHead{padding:10px;border-bottom:1px solid #27324a;font-size:13px;display:flex;align-items:center;justify-content:space-between}
.chatList{flex:1;overflow:auto;padding:10px}
.entry{padding:8px;border:1px solid #2c3958;border-radius:10px;margin-bottom:8px;background:#0d1423}
.entry.stt{border-color:#60a5fa}
.row{display:flex;gap:6px;padding:8px;border-top:1px solid #27324a;flex-wrap:wrap}
.btn{padding:8px 10px;border-radius:999px;border:1px solid #334161;background:#1a2238;color:#e8efff;cursor:pointer;display:flex;align-items:center;gap:8px}
.btn.small{padding:6px 10px}
.input{display:flex;gap:6px;padding:8px;border-top:1px solid #27324a}
textarea{flex:1;min-height:58px;background:#0b1220;color:#eaf1ff;border:1px solid #334161;border-radius:8px;padding:8px}
.led{width:10px;height:10px;border-radius:999px;display:inline-block}.r{background:#ef4444}.g{background:#22c55e}.blink{animation:bl 1s linear infinite}@keyframes bl{50%{opacity:.25}}
.badges{display:flex;gap:8px;flex-wrap:wrap}
.badge{display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid #334161;border-radius:999px;background:#121a2c}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace}
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div class="badges">
      <span class="badge"><span id="ledSock" class="led r"></span><span class="mono">SOCKET</span> <span id="stSock">…</span></span>
      <span class="badge"><span id="ledRtc" class="led r"></span><span class="mono">WEBRTC</span> <span id="stPc">new</span></span>
      <span class="badge"><span id="ledIce" class="led r"></span><span class="mono">ICE</span> <span id="stIce">new</span></span>
      <span class="badge"><span class="mono">Bitrate</span> <span id="stBr">0 kbps</span></span>
      <span class="badge"><span class="mono">Room</span> <span id="stRoom">default</span></span>
      <button id="aiBtn" class="btn small"><span id="aiLed" class="led r"></span><span id="aiTxt">AI: idle</span></button>
    </div>
  </div>
  <div class="main">
    <div class="videoWrap"><video id="watchVideo" autoplay playsinline controls></video></div>
    <div class="chat">
      <div class="chatHead">
        <span>Chat</span>
        <span id="roomStatus" style="font-size:12px;opacity:.8"></span>
      </div>
      <div id="chat" class="chatList"></div>
      <div class="row">
        <button id="attachBtn" class="btn small">Attach</button>
        <button id="webBtn" class="btn small">WebSearch</button>
        <button id="speakBtn" class="btn small">Speak last AI</button>
        <input id="file" type="file" hidden>
      </div>
      <div class="input">
        <textarea id="chatInput" placeholder="Message"></textarea>
        <button id="sendBtn" class="btn">Send</button>
      </div>
    </div>
  </div>
</div>

<script>
(() => {
  const params=new URLSearchParams(location.search);
  const room=(params.get('room')||'default').trim() || 'default';
  document.getElementById('stRoom').textContent = room;

  const socket=io(location.origin,{
    path:'/socket.io',
    transports:['websocket'],
    upgrade:false,
    query:{room,role:'watch'}
  });

  const video=document.getElementById('watchVideo');
  const chatEl=document.getElementById('chat');
  const input=document.getElementById('chatInput');

  const ledSock=document.getElementById('ledSock'), stSock=document.getElementById('stSock');
  const ledRtc=document.getElementById('ledRtc'), ledIce=document.getElementById('ledIce');
  const stPc=document.getElementById('stPc'), stIce=document.getElementById('stIce'), stBr=document.getElementById('stBr');
  const roomStatus=document.getElementById('roomStatus');
  const aiLed=document.getElementById('aiLed'), aiTxt=document.getElementById('aiTxt');

  let seen=new Set();
  let pc=null;
  let watchJoinSent=false;
  let lastAiText='';
  let makingAnswer=false;

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
    if(seen.size>900){ const a=[...seen].slice(-400); seen=new Set(a); }
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
      const r=await fetch('/webrtc/ice-config',{cache:'no-store'});
      if(!r.ok) throw new Error('bad status '+r.status);
      const j=await r.json();
      if(j && j.iceServers && Array.isArray(j.iceServers)) return j.iceServers;
    }catch(e){
      console.warn('[watch] ice-config fetch failed, STUN-only', e);
    }
    return [{urls:'stun:stun.l.google.com:19302'}];
  }

  async function buildPc(){
    const iceServers = await getIceServers();
    pc=new RTCPeerConnection({iceServers});

    // recv transceivers first (stable)
    pc.addTransceiver('video',{direction:'recvonly'});
    pc.addTransceiver('audio',{direction:'recvonly'});

    pc.onicecandidate=(e)=>{ if(e.candidate){ socket.emit('watch_ice',{candidate:e.candidate}); } };
    pc.onconnectionstatechange=()=>{
      stPc.textContent=pc.connectionState;
      setLed(ledRtc, pc.connectionState==='connected', pc.connectionState==='connecting');
      if(['failed','disconnected'].includes(pc.connectionState)){
        watchJoinSent=false;
        requestOffer();
      }
    };
    pc.oniceconnectionstatechange=()=>{
      stIce.textContent=pc.iceConnectionState;
      setLed(ledIce, pc.iceConnectionState==='connected' || pc.iceConnectionState==='completed', pc.iceConnectionState==='checking');
    };
    pc.ontrack=(e)=>{
      try{
        if(e.streams && e.streams.length){
          video.srcObject=e.streams[0];
        }else{
          if(!video._s){ video._s=new MediaStream(); video.srcObject=video._s; }
          video._s.addTrack(e.track);
        }
        video.play().catch(()=>{});
      }catch(err){}
    };
  }

  async function requestOffer(){
    if(watchJoinSent) return;
    if(!pc || pc.connectionState==='closed') await buildPc();
    watchJoinSent=true;
    socket.emit('watch_join',{});
  }

  // bitrate
  let _lastBytes=0,_lastTs=performance.now();
  setInterval(async()=>{
    if(!pc) return;
    try{
      const stats=await pc.getStats();
      let bytes=0;
      stats.forEach(r=>{ if(r.type==='inbound-rtp'){ bytes += (r.bytesReceived||0); }});
      const now=performance.now(); const dt=Math.max(0.001,(now-_lastTs)/1000);
      const kbps=((bytes-_lastBytes)*8/1000)/dt;
      _lastBytes=bytes; _lastTs=now;
      stBr.textContent = `${Math.max(0,Math.round(kbps))} kbps`;
    }catch(e){}
  },2000);

  // AI toggle (client-side + server best-effort)
  let aiActive=false;
  function setAiActive(on){
    aiActive=!!on;
    aiTxt.textContent='AI: '+(aiActive?'active':'idle');
    setLed(aiLed, aiActive, aiActive);
    socket.emit('set_room_settings',{ai_enabled: aiActive});
    socket.emit('ai_settings_set',{ai_power: aiActive, ai_mode: aiActive?'active':'idle'});
  }

  socket.on('connect', ()=>{
    stSock.textContent='connected';
    setLed(ledSock,true,true);
    setAiActive(true);
    watchJoinSent=false;
    requestOffer();
  });
  socket.on('disconnect', ()=>{
    stSock.textContent='disconnected';
    setLed(ledSock,false);
  });

  socket.on('watch_offer', async(offer)=>{
    if(makingAnswer) return;
    makingAnswer=true;
    try{
      if(!pc) await buildPc();
      await pc.setRemoteDescription(offer);
      const ans=await pc.createAnswer();
      await pc.setLocalDescription(ans);
      socket.emit('watch_answer',{sdp:pc.localDescription.sdp,type:pc.localDescription.type});
    }catch(e){
      watchJoinSent=false;
      requestOffer();
    }finally{
      makingAnswer=false;
    }
  });

  socket.on('watch_ice_server', async(p)=>{
    try{
      if(!pc) return;
      const cand = p && p.candidate ? p.candidate : p;
      if(cand) await pc.addIceCandidate(cand);
    }catch(e){}
  });

  socket.on('waiting_for_broadcaster', ()=> addMsg({sender:'system',role:'system',text:'Waiting for broadcaster…',ts:Date.now()}));
  socket.on('stream_started', ()=>{ watchJoinSent=false; requestOffer(); });

  socket.on('chat_message', addMsg);
  socket.on('stt_text', (p)=> addMsg({sender:'stt',role:'broadcaster_stt',text:p.text,ts:p.ts||Date.now()}));
  socket.on('room_status', (p)=>{
    if(!p) return;
    roomStatus.textContent = `viewers:${p.viewer_count||0} broadcaster:${p.broadcaster_present?'yes':'no'}`;
  });
  socket.on('ai_tts_audio', (p)=>{
    if(p && p.url){
      try{ const a=new Audio(p.url); a.play().catch(()=>{}); }catch(e){}
    }
  });

  document.getElementById('aiBtn').onclick=()=> setAiActive(!aiActive);

  document.getElementById('sendBtn').onclick=()=>{
    const t=input.value.trim();
    if(!t) return;
    socket.emit('chat_message',{text:t,ts:Date.now()});
    input.value='';
  };

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

  window.addEventListener('beforeunload', ()=>{ try{ if(pc) pc.close(); }catch(e){} });
})();
</script>
</body>
</html>

EOF_STATIC_WATCH_HTML




write_file "static/css_app.css" <<'EOF_STATIC_CSS_APP_CSS'
/* static/css_app.css - THEME + LAYOUT + STATUS LIGHTS */

/* Theme variables */
:root{
  --main-blue: #0D47A1;
  --vivid-green: #00E676;
  --bright-orange: #FF6D00;
  --white: #ffffff;
  --muted: #0f1724;
  --panel: #0b1220;
  --accent-outline: rgba(255,255,255,0.08);
  --text-high: #e8eef9;
  --text-low: #cbd5e1;
  --status-yellow: #FFD600;
  --status-red: #EF4444;
  --status-green: #22C55E;
  --status-blue: var(--main-blue);
  --glass: rgba(255,255,255,0.03);

  --blink-slow: 1.6s;
  --blink-normal: 1s;
  --blink-fast: 0.45s;
  --radius-lg: 12px;
}

/* Base */
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0;
  font-family:Inter, "Segoe UI", Roboto, Arial, sans-serif;
  background: linear-gradient(180deg,#061022 0%, #081025 100%);
  color:var(--text-high);
  -webkit-font-smoothing:antialiased;
}

/* App container */
.wrap{
  max-width:1300px;
  margin:14px auto;
  padding:14px;
}

/* Header */
.header{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-bottom:12px;
}
.brand{
  display:flex;
  gap:12px;
  align-items:center;
}
.brand h1{font-size:1.15rem;margin:0;color:var(--main-blue)}
.small-muted{color:var(--text-low);font-size:0.9rem}

/* Layout */
.main-grid{
  display:grid;
  grid-template-columns: minmax(320px, 1fr) 360px;
  gap:14px;
  align-items:start;
}
@media (max-width:980px){
  .main-grid{grid-template-columns: 1fr; padding-bottom:12px}
}

/* Video card */
.video-card{
  background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
  border-radius: var(--radius-lg);
  padding:10px;
  border: 1px solid var(--accent-outline);
  box-shadow: 0 6px 20px rgba(2,6,23,0.6);
  display:flex;
  flex-direction:column;
  gap:10px;
}

/* video container keeps aspect ratio while shrinking */
.video-frame{
  position:relative;
  width:100%;
  padding-top:56.25%; /* 16:9 */
  background:#000;
  border-radius:10px;
  overflow:hidden;
}
.video-frame video{
  position:absolute;
  inset:0;
  width:100%;
  height:100%;
  object-fit:cover;
  background:#000;
}

/* pip */
#pipVideo{
  position:absolute;
  width:220px;
  height:140px;
  right:12px;
  bottom:12px;
  border-radius:8px;
  border:2px solid rgba(255,255,255,0.06);
  object-fit:cover;
  cursor:grab;
  z-index:20;
}

/* controls row under video */
.controls{
  display:flex;
  gap:8px;
  align-items:center;
  flex-wrap:wrap;
}
.btn{
  background:linear-gradient(180deg,var(--panel), rgba(255,255,255,0.02));
  color:var(--text-high);
  padding:10px 14px;
  border-radius:10px;
  border:1px solid var(--accent-outline);
  font-weight:600;
  cursor:pointer;
  transition: transform .08s ease, box-shadow .12s ease;
}
.btn:active{ transform: translateY(2px); }
.btn.ghost{ background:transparent; border:1px dashed rgba(255,255,255,0.04);}

/* status lights row */
.status-row{ display:flex; gap:12px; align-items:center; margin-left:auto; }
.status-pill{ display:flex; gap:8px; align-items:center; background:var(--glass); padding:6px 10px; border-radius:999px; border:1px solid rgba(255,255,255,0.03); }
.status-light{
  width:18px; height:18px; border-radius:50%;
  border:2px solid rgba(0,0,0,0.35); box-shadow:0 4px 14px rgba(2,6,23,0.6);
  transition: transform .12s ease, box-shadow .2s ease, opacity .18s ease, background-color .18s ease;
  display:inline-block;
}
.status-light:active{ transform: translateY(1px) scale(.98) }

/* glow helper (uses currentColor) */
.status-glow{ box-shadow:0 0 12px 3px currentColor; }

/* blinking animation */
@keyframes blink {
  0%,50%,100%{opacity:1}
  25%,75%{opacity:0.28}
}
.blink-slow{ animation: blink var(--blink-slow) infinite; }
.blink-normal{ animation: blink var(--blink-normal) infinite; }
.blink-fast{ animation: blink var(--blink-fast) infinite; }

/* colors (set via classes or inline color) */
.c-green{ background:var(--status-green); color:var(--status-green); }
.c-red{ background:var(--status-red); color:var(--status-red); }
.c-yellow{ background:var(--status-yellow); color:var(--status-yellow); }
.c-orange{ background:var(--bright-orange); color:var(--bright-orange); }
.c-blue{ background:var(--status-blue); color:var(--status-blue); }

/* Chat column */
.chat-card{
  display:flex; flex-direction:column; gap:8px;
  background:linear-gradient(180deg, rgba(255,255,255,0.01), rgba(255,255,255,0.02));
  padding:10px; border-radius:var(--radius-lg); border:1px solid var(--accent-outline);
  height:100%;
  min-height:320px;
}
.chat-list{
  background:transparent;
  overflow:auto;
  border-radius:8px;
  padding:8px;
  height:56vh;
  max-height:66vh;
}
@media (max-width:980px){ .chat-list{ height:34vh; } }
.chat-entry{ padding:8px; border-bottom:1px solid rgba(255,255,255,0.02); }
.chat-entry small{ color:var(--text-low); margin-left:8px; font-weight:500; }

/* chat input area */
.chat-input{ display:flex; gap:8px; align-items:center; margin-top:auto; }
.chat-text{ flex:1; min-height:56px; border-radius:10px; padding:10px; background:var(--panel); color:var(--text-high); border:1px solid var(--accent-outline); resize:vertical; }
.icon-btn{ width:44px; height:44px; border-radius:8px; display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--accent-outline); background:transparent; cursor:pointer; }

/* message media */
.msg-media{ max-width:100%; margin-top:8px; border-radius:8px; }

/* helper utilities */
.row{ display:flex; gap:8px; align-items:center; }
.center{ display:flex; justify-content:center; align-items:center; }

/* tiny label */
.status-label{ font-size:0.9rem; color:var(--text-low); margin-left:6px; font-weight:600; }
EOF_STATIC_CSS_APP_CSS





write_file "static/theme.css" <<'EOF_STATIC_THEME_CSS'
/* Root theme variables */
:root {
    --main-blue: #1E3A8A;
    --vivid-green: #10B981;
    --bright-orange: #F97316;
    --high-white: #FFFFFF;
    --high-grey: #E5E7EB;
    --high-black: #111827;
    --status-yellow: #FACC15;
    --status-red: #EF4444;
    --status-green: #22C55E;

    /* Default blink durations */
    --blink-slow: 1.5s;
    --blink-normal: 1s;
    --blink-fast: 0.5s;
}

/* Base body and header */
body {
    font-family: "Segoe UI", Roboto, sans-serif;
    background-color: var(--high-grey);
    color: var(--high-black);
    margin: 0;
    padding: 0;
}

.page-header {
    background-color: var(--main-blue);
    color: var(--high-white);
    padding: 1rem;
    text-align: center;
}

/* Buttons */
button {
    cursor: pointer;
    border: 2px solid var(--high-black);
    border-radius: 0.5rem;
    padding: 0.5rem 1rem;
    font-weight: bold;
    background-color: var(--high-white);
    transition: 0.2s all ease-in-out;
}

button:active {
    transform: translateY(2px);
    box-shadow: inset 0 0 5px rgba(0,0,0,0.3);
}

/* Status Cards */
.status-card {
    background-color: var(--high-white);
    border: 2px solid var(--high-black);
    border-radius: 0.75rem;
    padding: 1rem;
    margin: 1rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    box-shadow: 2px 2px 8px rgba(0,0,0,0.2);
}

/* Status Light Base */
.status-light {
    display: inline-block;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    border: 2px solid var(--high-black);
    box-shadow: 0 0 5px rgba(0,0,0,0.5);
    vertical-align: middle;
    transition: background-color 0.4s ease, box-shadow 0.4s ease, transform 0.2s ease;
}

/* Glow effects */
.status-glow {
    box-shadow: 0 0 10px 2px currentColor;
}

/* Blinking keyframes (variable speed) */
@keyframes blink {
    0%, 50%, 100% { opacity: 1; }
    25%, 75% { opacity: 0.3; }
}

/* Status Light Colors */
.status-green { background-color: var(--status-green); color: var(--status-green); }
.status-red { background-color: var(--status-red); color: var(--status-red); }
.status-yellow { background-color: var(--status-yellow); color: var(--status-yellow); }
.status-orange { background-color: var(--bright-orange); color: var(--bright-orange); }
.status-blue { background-color: var(--main-blue); color: var(--main-blue); }

/* Status Captions */
.status-caption {
    font-weight: bold;
    color: var(--high-black);
    vertical-align: middle;
}
EOF_STATIC_THEME_CSS

# -------------------------
# CSS (wrapper)
# -------------------------
write_file "static/css.css" <<'EOF_STATIC_CSS_CSS'
@import url("/static/theme.css");
@import url("/static/css_app.css");
EOF_STATIC_CSS_CSS



write_file "static/status_dashboard.html" <<'EOF_STATIC_STATUS_DASHBOARD_HTML'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Themed Status Dashboard</title>
<link rel="stylesheet" href="/static/theme.css">
</head>
<body>

<div class="page-header">
    <h1>Broadcast Status Dashboard</h1>
</div>

<div class="status-card">
    <div id="light1" class="status-light status-green status-glow"></div>
    <span id="caption1" class="status-caption">Online</span>
    <button onclick="toggleStatus('light1','caption1')">Toggle</button>
</div>

<div class="status-card">
    <div id="light2" class="status-light status-yellow"></div>
    <span id="caption2" class="status-caption">Standby</span>
    <button onclick="toggleStatus('light2','caption2')">Toggle</button>
</div>

<div class="status-card">
    <div id="light3" class="status-light status-red"></div>
    <span id="caption3" class="status-caption">Offline</span>
    <button onclick="toggleStatus('light3','caption3')">Toggle</button>
</div>

<script>
const statusStates = [
    {color: 'status-green', text: 'Online', blink: 'blink', glow: true, speed: 'var(--blink-normal)'},
    {color: 'status-yellow', text: 'Standby', blink: 'blink', glow: false, speed: 'var(--blink-slow)'},
    {color: 'status-red', text: 'Offline', blink: 'blink', glow: true, speed: 'var(--blink-fast)'},
    {color: 'status-orange', text: 'Busy', blink: 'blink', glow: true, speed: 'var(--blink-normal)'}
];

function toggleStatus(lightId, captionId) {
    const light = document.getElementById(lightId);
    const caption = document.getElementById(captionId);

    let currentIndex = statusStates.findIndex(s => light.classList.contains(s.color));
    light.classList.remove(statusStates[currentIndex].color);
    light.classList.remove('status-glow');

    let nextIndex = (currentIndex + 1) % statusStates.length;
    let nextState = statusStates[nextIndex];

    light.classList.add(nextState.color);
    caption.textContent = nextState.text;

    // Apply glow if needed
    if(nextState.glow) light.classList.add('status-glow');

    // Apply blink animation with custom speed
    light.style.animation = `${nextState.blink} ${nextState.speed} infinite`;
}
</script>

</body>
</html>
EOF_STATIC_STATUS_DASHBOARD_HTML



write_file "setup_vertex_ai.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

PROJECT_ID="${VERTEX_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
SERVICE_ACCOUNT_NAME="${VERTEX_SERVICE_ACCOUNT_NAME:-${VERTEX_SERVICE_ACCOUNT:-vertex-backend}}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME%@*}"
KEY_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/gcp-key.json}"

if [ -z "$PROJECT_ID" ]; then
  echo "VERTEX_PROJECT is empty; set it in .env first." >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI not found." >&2
  exit 1
fi

gcloud --quiet config set project "$PROJECT_ID" >/dev/null
for api in aiplatform.googleapis.com speech.googleapis.com texttospeech.googleapis.com iam.googleapis.com iamcredentials.googleapis.com; do
  gcloud services enable "$api" --project="$PROJECT_ID" >/dev/null
done

enabled="$(gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)')"
for api in aiplatform.googleapis.com speech.googleapis.com texttospeech.googleapis.com iam.googleapis.com iamcredentials.googleapis.com; do
  if ! grep -qx "$api" <<<"$enabled"; then
    echo "Required API not enabled: $api" >&2
    exit 1
  fi
done

SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" --project="$PROJECT_ID" --display-name="Vertex Broadcast Service Account"
fi
for role in roles/aiplatform.user roles/speech.client; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_EMAIL}" --role="$role" >/dev/null 2>&1 || true
done

mkdir -p "$(dirname "$KEY_FILE")"
if [ ! -f "$KEY_FILE" ]; then
  gcloud --quiet iam service-accounts keys create "$KEY_FILE" --iam-account "$SA_EMAIL" --project "$PROJECT_ID"
fi

chmod 600 "$KEY_FILE" || true
export GOOGLE_APPLICATION_CREDENTIALS="$KEY_FILE"
echo "GOOGLE_APPLICATION_CREDENTIALS=$GOOGLE_APPLICATION_CREDENTIALS"
echo "Run: sudo systemctl restart broadcast.service"
SH
chmod +x "${APP_DIR}/setup_vertex_ai.sh"

write_file "troubleshoot_broadcast.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")" && pwd)}"
SYSTEMD_UNIT="${SYSTEMD_UNIT_NAME:-broadcast.service}"

run_check() {
  local title="$1"
  shift
  echo
  echo "=============================="
  echo "$title"
  echo "=============================="
  set +e
  "$@"
  local rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    echo "[warn] command failed with exit $rc: $*"
  fi
}

echo "Broadcast diagnostics starting..."

echo "=============================="
echo "1) Hypercorn last 50 log lines"
echo "=============================="
if [ -f "$APP_DIR/hypercorn.log" ]; then
  tail -n 50 "$APP_DIR/hypercorn.log"
else
  echo "No hypercorn.log found at $APP_DIR"
fi

run_check "2) Systemd service status" sudo systemctl status "$SYSTEMD_UNIT" --no-pager
run_check "3) Recent service journal logs (last 100 lines)" sudo journalctl -u "$SYSTEMD_UNIT" -n 100 --no-pager
run_check "4) Coturn server status" sudo systemctl status coturn --no-pager
run_check "4b) Last 50 coturn log lines" sudo journalctl -u coturn -n 50 --no-pager

echo ""
echo "=============================="
echo "5) Verify GOOGLE_APPLICATION_CREDENTIALS in running Hypercorn"
echo "=============================="
pid="$(pgrep -f "hypercorn main:asgi_app" | head -n1 || true)"
if [ -n "$pid" ]; then
  echo "PID=$pid"
  set +e
  sudo tr '\0' '\n' < "/proc/$pid/environ" | grep GOOGLE_APPLICATION_CREDENTIALS
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    echo "env not set in process"
  fi
else
  echo "Hypercorn not running"
fi

echo ""
echo "=============================="
echo "6) Check ICE candidates (browser-side)"
echo "=============================="
echo "Open browser console on /watch or /broadcast and run:"
echo "  pc.getStats().then(stats => console.log([...stats.values()]));"
echo "Check candidate pair state, bytesSent/bytesReceived, and dtlsState"

echo ""
echo "=============================="
echo "7) Quick network checks"
echo "=============================="
echo "TURN UDP reachability from remote client:"
echo "  nc -vuz <server_ip> 3478"
echo "Socket.IO websocket probe (local):"
echo "  wscat -c ws://127.0.0.1:8000/socket.io/?EIO=4&transport=websocket"

echo ""
echo "=============================="
echo "8) AI/Vertex quick checks"
echo "=============================="
if [ -f "$APP_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$APP_DIR/.env"
  set +a
  echo "VERTEX_PROJECT=${VERTEX_PROJECT:-<unset>}"
  echo "GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS:-<unset>}"
  if [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ -f "${GOOGLE_APPLICATION_CREDENTIALS}" ]; then
    echo "Credentials file exists"
  else
    echo "Credentials file missing"
  fi
else
  echo "No .env found at $APP_DIR/.env"
fi

run_check "8b) Local AI chat endpoint" curl -sS -X POST http://127.0.0.1:8000/ai/chat -H 'Content-Type: application/json' -d '{"text":"hello from diagnostics"}'

echo ""
echo "=============================="
echo "9) STT chatroom manual verification"
echo "=============================="
echo "1) Open /broadcast in one browser and /watch in another"
echo "2) Speak into broadcaster microphone for 10+ seconds"
echo "3) Confirm 'stt' transcript messages appear in both chat panes"
echo "4) If no transcripts: check section 5 env + section 4 coturn + browser mic permissions"

echo ""
echo "=============================="
echo "End of Troubleshooting Script"
echo "=============================="
SH
chmod +x "${APP_DIR}/troubleshoot_broadcast.sh"

write_file "install_and_setup_venv.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
BASEDIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASEDIR"
python3 -m venv venv
source venv/bin/activate
pip install --progress-bar off --upgrade pip setuptools wheel
pip install --progress-bar off -r requirements.txt
SH
chmod +x "${APP_DIR}/install_and_setup_venv.sh"

write_file "run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
pkill -f "hypercorn main:asgi_app" || true
if [ -x "./venv/bin/hypercorn" ]; then
  CMD="./venv/bin/hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1 --log-level info"
else
  CMD="hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1 --log-level info"
fi
nohup bash -lc "$CMD" > "$BASE_DIR/hypercorn.log" 2>&1 &
echo "Started hypercorn"
SH
chmod +x "${APP_DIR}/run.sh"

write_file "dev.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
if [ -f .env ]; then
  set -a; source ./.env; set +a
fi
python3 - <<'PY2'
import os
print('GOOGLE_APPLICATION_CREDENTIALS=', os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))
print('GOOGLE_API_KEY set=', bool(os.getenv('GOOGLE_API_KEY')))
print('TURN_URL=', os.getenv('TURN_URL'))
PY2
exec hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1 --reload --log-level debug
SH
chmod +x "${APP_DIR}/dev.sh"

write_file "doctor.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
UNIT="${SYSTEMD_UNIT_NAME:-broadcast.service}"
sudo systemctl status "$UNIT" --no-pager || true
sudo systemctl status coturn --no-pager || true
ss -lntu | rg ':(8000|3478|5349|49160|49200)' || true
if [ -f .env ]; then
  rg -n '^(GOOGLE_APPLICATION_CREDENTIALS|GOOGLE_API_KEY|TURN_URL|VERTEX_PROJECT|AI_FALLBACK)=' .env || true
fi
SH
chmod +x "${APP_DIR}/doctor.sh"

write_file "Makefile" <<'MK'
.PHONY: dev prod doctor vertex

dev:
	./dev.sh

prod:
	hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1 --log-level info

doctor:
	./doctor.sh

vertex:
	./setup_vertex_ai.sh
MK

patch_socketio_clients() {
  local files=("${APP_DIR}/static/watch.html" "${APP_DIR}/static/broadcast.html")
  local from='const socket = io();'
  local to='const socket = io(window.location.origin, { path: "/socket.io", transports: ["websocket"], upgrade: false });'
  local f
  for f in "${files[@]}"; do
    if [ ! -f "$f" ]; then
      err "Socket.IO client patch skipped (missing file): $f"
      continue
    fi
    if grep -Fq "$to" "$f"; then
      log "Socket.IO websocket-only already set in $(basename "$f")"
      continue
    fi
    if grep -Fq "$from" "$f"; then
      attempt "patch websocket-only socket init in $(basename "$f")" sed -i "s#${from}#${to}#g" "$f"
    else
      err "Socket.IO init pattern not found in $(basename "$f"); skipping"
    fi
  done
}

patch_socketio_server() {
  local py_file="${APP_DIR}/main.py"
  if [ ! -f "$py_file" ]; then
    err "Socket.IO server patch skipped (missing file): $py_file"
    return 0
  fi

  if grep -Fq 'transports=["websocket"]' "$py_file" && grep -Fq 'max_http_buffer_size=20000000' "$py_file"; then
    log "Socket.IO websocket-only server settings already present in main.py"
    return 0
  fi

  attempt "patch websocket-only AsyncServer config" perl -0pi -e 's/socketio\.AsyncServer\(\s*async_mode="asgi",\s*cors_allowed_origins="\*"\s*\)/socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*", transports=["websocket"], max_http_buffer_size=20000000)/g' "$py_file"

  if grep -Fq 'transports=["websocket"]' "$py_file" && grep -Fq 'max_http_buffer_size=20000000' "$py_file"; then
    log "Socket.IO server patch verification passed"
  else
    err "Socket.IO server patch verification did not find expected settings in main.py"
  fi
}

section "STATIC PATCHES"
patch_socketio_clients
patch_socketio_server

attempt "chmod home execute" sudo chmod o+x "${APP_HOME}"
attempt "chmod app dir execute" sudo chmod o+x "${APP_DIR}"
attempt "chmod static readable" sudo chmod -R o+r "${APP_DIR}/static"


section "NGINX"
if [ "$NGINX_ENABLED" = true ]; then
  NGINX_PATH="/etc/nginx/sites-available/broadcast"
  if [ -f "${CERT_DIR}/fullchain.pem" ] && [ -f "${CERT_DIR}/privkey.pem" ]; then
    sudo tee "${NGINX_PATH}" >/dev/null <<NGINXCONF
server {
    listen 443 ssl;
    server_name ${DOMAIN};
    ssl_certificate ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    location / {
        proxy_pass http://${HYPERCORN_BIND};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
    }
    location /static/ {
        alias ${APP_DIR}/static/;
        access_log off;
        expires -1;
    }
}
server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}
NGINXCONF
  else
    err "TLS certs missing at ${CERT_DIR}; writing HTTP-only nginx config"
    sudo tee "${NGINX_PATH}" >/dev/null <<NGINXCONF
server {
    listen 80;
    server_name ${DOMAIN};
    location / {
        proxy_pass http://${HYPERCORN_BIND};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
    }
    location /static/ {
        alias ${APP_DIR}/static/;
        access_log off;
        expires -1;
    }
}
NGINXCONF
  fi
  attempt "enable nginx site symlink" sudo ln -sf "${NGINX_PATH}" /etc/nginx/sites-enabled/broadcast
  attempt "nginx config test" sudo nginx -t
  attempt "restart nginx" sudo systemctl restart nginx
fi


section "SYSTEMD"
if [ "$SYSTEMD_ENABLED" = true ]; then
  SYSTEMD_PATH="/etc/systemd/system/${SYSTEMD_UNIT_NAME}"
  SYSTEMD_CREDS_PATH="${GOOGLE_APPLICATION_CREDENTIALS_PATH:-}"
  SYSTEMD_ENV_CREDENTIALS_LINE=""
  if [ -n "$SYSTEMD_CREDS_PATH" ]; then
    SYSTEMD_ENV_CREDENTIALS_LINE="Environment=\"GOOGLE_APPLICATION_CREDENTIALS=${SYSTEMD_CREDS_PATH}\""
  fi
  log "systemd runtime identity: GOOGLE_APPLICATION_CREDENTIALS=${SYSTEMD_CREDS_PATH:-<metadata/adc>}, VERTEX_PROJECT=${VERTEX_PROJECT:-<unset>}"
  attempt "write systemd unit" sudo tee "${SYSTEMD_PATH}" >/dev/null <<SYSTEMD
[Unit]
Description=Broadcast ASGI app (Hypercorn)
After=network.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=/home/jayson_tolleson/broadcast/.env
Environment=PYTHONUNBUFFERED=1
${SYSTEMD_ENV_CREDENTIALS_LINE}
ExecStart=/bin/bash -lc 'if [ -x "${APP_DIR}/venv/bin/hypercorn" ]; then exec ${APP_DIR}/venv/bin/hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1; else exec hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1; fi'
Restart=on-failure
RestartSec=10
LimitNOFILE=10000
LimitNPROC=5000
MemoryMax=2G
Type=simple

[Install]
WantedBy=multi-user.target
SYSTEMD
  attempt "systemd daemon-reload" sudo systemctl daemon-reload
  attempt "restart coturn after config updates" sudo systemctl restart coturn
  attempt "restart nginx after config updates" sudo systemctl restart nginx
  attempt "systemd enable service" sudo systemctl enable "${SYSTEMD_UNIT_NAME}"
  attempt "systemd restart service" sudo systemctl restart "${SYSTEMD_UNIT_NAME}"
fi

attempt "npm install socket.io-client" sudo npm install --no-progress --prefix "${APP_DIR}" socket.io-client
attempt "copy socket.io.js" sudo cp "${APP_DIR}/node_modules/socket.io-client/dist/socket.io.js" "${APP_DIR}/static/socket.io.js"
attempt "chmod socket.io.js" sudo chmod 644 "${APP_DIR}/static/socket.io.js"

rm -f "${ZIPNAME}" || true
( cd "${APP_DIR}" && zip -r "${ZIPNAME}" . >/dev/null 2>&1 ) || true
attempt "chown app dir" sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
attempt "restart broadcast after socket.io patches" bash -lc "sudo systemctl restart broadcast || sudo systemctl restart '${SYSTEMD_UNIT_NAME}'"
section "FINAL VERIFY"
attempt "install python virtualenv dependencies" bash -lc "cd '${APP_DIR}' && ./install_and_setup_venv.sh"
attempt "daemon-reload after dependency install" sudo systemctl daemon-reload
attempt "restart ${SYSTEMD_UNIT_NAME} after dependency install" sudo systemctl restart "${SYSTEMD_UNIT_NAME}"
attempt "show ${SYSTEMD_UNIT_NAME} status" sudo systemctl status "${SYSTEMD_UNIT_NAME}" --no-pager
attempt "show ${SYSTEMD_UNIT_NAME} recent logs" sudo journalctl -u "${SYSTEMD_UNIT_NAME}" -n 60 --no-pager -l
attempt "ffmpeg installed" bash -lc "command -v ffmpeg && ffmpeg -version | head -n 2"

AUTH_MODE="ADC"
AUTH_IDENTITY="metadata service account"
if [ -n "${GOOGLE_APPLICATION_CREDENTIALS_PATH:-}" ]; then
  AUTH_MODE="JSON"
  AUTH_IDENTITY="${GOOGLE_APPLICATION_CREDENTIALS_PATH}"
elif is_gce; then
  AUTH_IDENTITY="$(curl -fsS -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email" 2>/dev/null || echo metadata-unavailable)"
fi

done_box

# What changed:
# - Added bright ANSI theme with NO_COLOR / non-TTY auto-disable for clean logging.
# - Added --strict-auth flag and smoke_test_stt_auth() STT auth diagnostics.
# - Kept install behavior idempotent and non-fatal on STT auth issues unless strict mode is enabled.
