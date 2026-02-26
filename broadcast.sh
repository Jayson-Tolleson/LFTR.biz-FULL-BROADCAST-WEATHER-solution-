#!/usr/bin/env bash
set -euo pipefail
# ------------------ Defaults (override with flags) ------------------
APP_USER="jayson_tolleson"
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
ASSUME_YES=false
# ------------------ helpers ------------------
log() { echo "[installer] $*"; }
err() { echo "[installer][ERR] $*" >&2; }
attempt() {
  local name="$1"
  shift
  log "START: ${name}"
  set +e
  "$@"
  local rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    err "FAILED (continuing): ${name}"
  else
    log "OK: ${name}"
  fi
  return 0
}


gcloud_set_project_quiet() {
  local project_id="$1"
  if [ -z "${project_id:-}" ]; then
    err "gcloud_set_project_quiet: empty project id"
    return 1
  fi
  local out rc
  set +e
  out="$(gcloud --quiet config set project "$project_id" 2>&1)"
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    err "gcloud config set project failed for ${project_id}"
    echo "$out" >&2
    return $rc
  fi

  if [ -n "$out" ]; then
    echo "$out" | awk '!/lacks an '''environment''' tag/'
  fi

  ensure_environment_tag "$project_id"
  return 0
}
usage() {
  cat <<USAGE
Usage: $0 [options]
Options:
  --app-user=USER        (default: ${APP_USER})
  --app-dir=DIR          (default: ${APP_DIR})
  --domain=DOMAIN        (default: ${DOMAIN})
  --no-nginx             do not write nginx site config
  --no-systemd           do not write systemd unit
  --force                overwrite files without asking
  --yes                  assume yes to prompts
  -h, --help             show this help
USAGE
  exit 1
}
for arg in "$@"; do
  case "$arg" in
    --app-user=*) APP_USER="${arg#*=}" ;;
    --app-dir=*) APP_DIR="${arg#*=}" ;;
    --domain=*) DOMAIN="${arg#*=}" ;;
    --no-nginx) NGINX_ENABLED=false ;;
    --no-systemd) SYSTEMD_ENABLED=false ;;
    --force) FORCE=true ;;
    --yes) ASSUME_YES=true ;;
    -h|--help) usage ;;
    *) err "Unknown option: $arg"; usage ;;
  esac
done
APP_HOME="/home/${APP_USER}"
ZIPNAME="${APP_HOME}/broadcast_app_package.zip"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"

if [ "$ASSUME_YES" = false ] && [ "$FORCE" = false ]; then
  read -r -p "This will create/overwrite: ${APP_DIR}
Continue? [y/N] " ans
  if [[ "${ans,,}" != "y" ]]; then
    log "Aborted by user."; exit 1
  fi
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
  log "User ${APP_USER} does not exist. Creating..."
  attempt "create app user" sudo useradd --create-home --shell /bin/bash "${APP_USER}"
fi

if [ "$FORCE" = true ]; then
  rm -rf "${APP_DIR}"
fi
mkdir -p "${APP_DIR}"
attempt "ensure app dir ownership" sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

write_file() {
  local path="$APP_DIR/$1"
  mkdir -p "$(dirname "$path")"
  cat > "$path"
  chmod 644 "$path"
}

to_bool() {
  case "${1,,}" in
    n|no|0|false) echo "false" ;;
    ""|y|yes|1|true) echo "true" ;;
    *) echo "false" ;;
  esac
}

echo
echo "Configuring app runtime (.env) values. Press Enter to accept defaults."
read -r -p "Vertex Project ID [broadcasterfishmap]: " INPUT_VERTEX_PROJECT
read -r -p "Vertex Location [global]: " INPUT_VERTEX_LOCATION
read -r -p "Vertex Model Name [gemini-2.5-flash]: " INPUT_VERTEX_MODEL_NAME

# If you are on a GCE VM with a service account attached, you can leave this blank
read -r -p "Google Credentials JSON path (leave blank to use VM service account) [/home/${APP_USER}/gcp-key.json]: " INPUT_GOOGLE_CREDS

read -r -p "Google API key [optional]: " INPUT_GOOGLE_API_KEY
read -r -p "Vertex Service Account name [vertex-backend]: " INPUT_VERTEX_SA_NAME
read -r -p "Attempt IAM role grants for service account? [y/N]: " INPUT_VERTEX_GRANTS
read -r -p "TURN URL (turn:domain:3478) [leave blank to disable]: " INPUT_TURN_URL
read -r -p "TURN Username [webrtc]: " INPUT_TURN_USER
read -r -p "TURN Password [leave blank if none]: " INPUT_TURN_PASS

VERTEX_PROJECT="${INPUT_VERTEX_PROJECT:-broadcasterfishmap}"
VERTEX_LOCATION="${INPUT_VERTEX_LOCATION:-global}"
VERTEX_MODEL_NAME="${INPUT_VERTEX_MODEL_NAME:-gemini-2.5-flash}"
VERTEX_SA_NAME="${INPUT_VERTEX_SA_NAME:-vertex-backend}"
VERTEX_ENABLE_IAM_GRANTS="$(to_bool "${INPUT_VERTEX_GRANTS:-N}")"

# If blank, we will prefer VM service account (ADC via metadata) when on GCE.
DEFAULT_CREDS="/home/${APP_USER}/gcp-key.json"
if [ -z "${INPUT_GOOGLE_CREDS:-}" ]; then
  GOOGLE_APPLICATION_CREDENTIALS_PATH=""
else
  GOOGLE_APPLICATION_CREDENTIALS_PATH="${INPUT_GOOGLE_CREDS:-$DEFAULT_CREDS}"
fi

is_gce() {
  curl -s -m 1 -H "Metadata-Flavor: Google" "http://metadata.google.internal" >/dev/null 2>&1
}

ensure_environment_tag() {
  local project_id="$1"
  if [ -z "${project_id:-}" ]; then
    return 0
  fi
  if ! command -v gcloud >/dev/null 2>&1; then
    log "gcloud not found; skipping optional environment tag check"
    return 0
  fi

  local project_number
  if ! project_number="$(gcloud --quiet projects describe "$project_id" --format='value(projectNumber)' 2>/dev/null)" || [ -z "$project_number" ]; then
    err "Skipping environment tag check (unable to read project number; optional)."
    return 0
  fi

  local parent="//cloudresourcemanager.googleapis.com/projects/${project_number}"
  local existing_bindings
  if existing_bindings="$(gcloud --quiet resource-manager tags bindings list --parent="$parent" --format='value(tagValue)' 2>/dev/null)"; then
    if echo "$existing_bindings" | grep -qi 'environment'; then
      log "environment tag already set on project ${project_id}"
      return 0
    fi
  else
    err "Skipping environment tag binding check (insufficient permissions). This is optional and does not affect install."
    return 0
  fi

  local tag_key_resource
  tag_key_resource="$(gcloud --quiet resource-manager tags keys list --format='value(name)' --filter='shortName=environment' 2>/dev/null | head -n1 || true)"
  if [ -z "$tag_key_resource" ]; then
    log "No TagKey with shortName=environment found; skipping optional tag binding"
    return 0
  fi

  local preferred_shortnames=(Production Development Staging Test)
  local tag_value_resource=""
  local short
  for short in "${preferred_shortnames[@]}"; do
    tag_value_resource="$(gcloud --quiet resource-manager tags values list --parent="$tag_key_resource" --format='value(name)' --filter="shortName=${short}" 2>/dev/null | head -n1 || true)"
    if [ -n "$tag_value_resource" ]; then
      break
    fi
  done

  if [ -z "$tag_value_resource" ]; then
    log "No preferred environment TagValue (Production/Development/Staging/Test) found; skipping optional tag binding"
    return 0
  fi

  if gcloud --quiet resource-manager tags bindings create --parent="$parent" --tag-value="$tag_value_resource" >/dev/null 2>&1; then
    log "Bound environment tag (${tag_value_resource}) to project ${project_id}"
  else
    err "Skipping environment tag binding (insufficient permissions). This is optional and does not affect install."
  fi
}

normalize_turn_url() {
  local raw="${1:-}"
  raw="${raw## }"
  raw="${raw%% }"
  if [ -z "$raw" ]; then
    echo ""
    return
  fi
  if [[ "$raw" =~ ^(stun:|turn:|turns:) ]]; then
    echo "$raw"
  else
    echo "turn:${raw}:3478?transport=udp"
  fi
}

setup_vertex_credentials() {
  local project_id="$1"
  local creds_path="$2"   # may be blank to use VM SA on GCE
  local sa_name="$3"
  local do_grants="$4"
  local sa_email="${sa_name}@${project_id}.iam.gserviceaccount.com"

  if [ -z "$project_id" ]; then
    err "VERTEX_PROJECT is empty; skipping Google API setup"
    return 0
  fi

  # If on GCE and creds_path is blank: prefer ADC via metadata server
  if is_gce && [ -z "${creds_path:-}" ]; then
    log "GCE detected and no credentials JSON provided; using VM service account (ADC via metadata)."

    # NOTE: This can still fail if the VM was created with insufficient OAuth scopes (needs cloud-platform).
    if command -v gcloud >/dev/null 2>&1; then
      local scopes
      scopes="$(curl -s -H "Metadata-Flavor: Google"         "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/scopes" || true)"
      if ! echo "$scopes" | grep -q "https://www.googleapis.com/auth/cloud-platform"; then
        err "WARNING: VM service account scopes do NOT include cloud-platform. You may hit 'insufficient authentication scopes'."
        err "Fix: recreate VM or adjust access scopes to 'Allow full access to all Cloud APIs' (cloud-platform)."
      fi

      # Even when using VM ADC, the APIs still must be enabled in the project.
      log "Ensuring required Google APIs are enabled for project ${project_id} (VM ADC mode)"
      if gcloud_set_project_quiet "$project_id"; then
        log "OK: gcloud config set project"
      else
        err "FAILED (continuing): gcloud config set project"
      fi
      attempt "enable Service Usage API" gcloud --quiet services enable serviceusage.googleapis.com
      attempt "enable Vertex AI API" gcloud --quiet services enable aiplatform.googleapis.com
      attempt "enable Speech API" gcloud --quiet services enable speech.googleapis.com
      attempt "enable Text-to-Speech API" gcloud --quiet services enable texttospeech.googleapis.com
      attempt "enable IAM Credentials API" gcloud --quiet services enable iamcredentials.googleapis.com
    else
      err "gcloud not found; cannot enable APIs automatically. If AI/TTS/STT fails, enable APIs in GCP console."
    fi

    return 0
  fi

  # If creds_path points to an existing file, we’re done
  if [ -n "${creds_path:-}" ] && [ -f "$creds_path" ]; then
    log "Found existing Google credentials at ${creds_path}"
    return 0
  fi

  if ! command -v gcloud >/dev/null 2>&1; then
    err "gcloud not found and no credentials JSON; AI/TTS/STT will not work until you provide credentials or run on GCE with SA."
    return 0
  fi

  log "Attempting automatic Google API setup for project ${project_id}"
      if gcloud_set_project_quiet "$project_id"; then
        log "OK: gcloud config set project"
      else
        err "FAILED (continuing): gcloud config set project"
      fi

  # Enable APIs needed for: Vertex/Gemini, Speech-to-Text, Text-to-Speech
  attempt "enable Service Usage API" gcloud --quiet services enable serviceusage.googleapis.com
  attempt "enable Vertex AI API" gcloud --quiet services enable aiplatform.googleapis.com
  attempt "enable Speech API" gcloud --quiet services enable speech.googleapis.com
  attempt "enable Text-to-Speech API" gcloud --quiet services enable texttospeech.googleapis.com
  attempt "enable IAM Credentials API" gcloud --quiet services enable iamcredentials.googleapis.com

  if gcloud --quiet iam service-accounts describe "$sa_email" >/dev/null 2>&1; then
    log "Service account ${sa_email} already exists"
  else
    attempt "create service account ${sa_name}"       gcloud --quiet iam service-accounts create "$sa_name"       --display-name="Broadcast AI Service Account"
  fi

  if [ "$do_grants" = "true" ]; then
    if gcloud --quiet projects get-iam-policy "$project_id" >/dev/null 2>&1; then
      # Vertex/Gemini via Vertex
      attempt "grant aiplatform.user"         gcloud --quiet projects add-iam-policy-binding "$project_id"         --member="serviceAccount:${sa_email}" --role="roles/aiplatform.user"

      # STT
      attempt "grant speech.client"         gcloud --quiet projects add-iam-policy-binding "$project_id"         --member="serviceAccount:${sa_email}" --role="roles/speech.client"

      # TTS (correct role)
      attempt "grant texttospeech.user"         gcloud --quiet projects add-iam-policy-binding "$project_id"         --member="serviceAccount:${sa_email}" --role="roles/texttospeech.user"

      # Helps when enabling/consuming services via gcloud/serviceusage
      attempt "grant serviceusage.serviceUsageConsumer"         gcloud --quiet projects add-iam-policy-binding "$project_id"         --member="serviceAccount:${sa_email}" --role="roles/serviceusage.serviceUsageConsumer"
    else
      err "Skipping IAM role grants: current identity cannot read/modify project IAM policy."
    fi
  else
    log "Skipping IAM role grants (you selected no)."
  fi

  # Create key only if we are using JSON credentials mode
  if [ -n "${creds_path:-}" ]; then
    mkdir -p "$(dirname "$creds_path")"
    if [ ! -f "$creds_path" ]; then
      attempt "create service account key"         gcloud --quiet iam service-accounts keys create "$creds_path"         --iam-account="$sa_email"
    fi

    if [ -f "$creds_path" ]; then
      chmod 600 "$creds_path" || true
      attempt "chown credentials" sudo chown "${APP_USER}:${APP_USER}" "$creds_path"
      log "Google credentials ready at ${creds_path}"
    else
      err "Automatic credential provisioning did not create ${creds_path}. Provide credentials manually."
    fi
  fi
}

setup_vertex_credentials "$VERTEX_PROJECT" "$GOOGLE_APPLICATION_CREDENTIALS_PATH" "$VERTEX_SA_NAME" "$VERTEX_ENABLE_IAM_GRANTS"
TURN_URL_NORMALIZED="$(normalize_turn_url "${INPUT_TURN_URL}")"

# Write .env (only set GOOGLE_APPLICATION_CREDENTIALS if we actually have a JSON path)
{
  echo "VERTEX_PROJECT=${VERTEX_PROJECT}"
  echo "GOOGLE_CLOUD_PROJECT=${VERTEX_PROJECT}"
  echo "VERTEX_LOCATION=${VERTEX_LOCATION}"
  echo "VERTEX_MODEL_NAME=${VERTEX_MODEL_NAME}"
  echo "VERTEX_MODEL=${VERTEX_MODEL_NAME}"
  echo "AI_FALLBACK=false"
  if [ -n "${GOOGLE_APPLICATION_CREDENTIALS_PATH:-}" ]; then
    echo "GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS_PATH}"
  fi
  echo "GOOGLE_API_KEY=${INPUT_GOOGLE_API_KEY}"
  echo "VERTEX_SERVICE_ACCOUNT=${VERTEX_SA_NAME}@${VERTEX_PROJECT}.iam.gserviceaccount.com"
  echo "TURN_URL=${TURN_URL_NORMALIZED}"
  echo "TURN_USERNAME=${INPUT_TURN_USER:-webrtc}"
  echo "TURN_PASSWORD=${INPUT_TURN_PASS}"
} > "${APP_DIR}/.env"

chmod 600 "${APP_DIR}/.env"
attempt "chown env file" sudo chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"

if command -v apt-get >/dev/null 2>&1; then
  log "Installing Debian/Ubuntu dependencies"
  attempt "apt-get update" sudo apt-get update -y
  attempt "apt-get install runtime packages" sudo apt-get install -y ffmpeg build-essential pkg-config libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev libavfilter-dev libswscale-dev libswresample-dev libssl-dev python3-dev nginx python3-certbot-nginx python3-venv npm ufw coturn certbot curl
fi

attempt "ufw allow OpenSSH" sudo ufw allow OpenSSH
attempt "ufw allow 3478/tcp" sudo ufw allow 3478/tcp
attempt "ufw allow 3478/udp" sudo ufw allow 3478/udp
attempt "ufw allow 5349/tcp" sudo ufw allow 5349/tcp
attempt "ufw allow 49160:49200/udp" sudo ufw allow 49160:49200/udp
attempt "ufw allow 80/tcp" sudo ufw allow 80/tcp
attempt "ufw allow 443/tcp" sudo ufw allow 443/tcp
attempt "ufw enable" sudo ufw --force enable

if command -v gcloud >/dev/null 2>&1 && curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal >/dev/null 2>&1; then
  log "Detected Google Compute Engine"
  ZONE=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone | awk -F/ '{print $NF}')
  INSTANCE_NAME=$(hostname)
  GCP_NETWORK=$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$VERTEX_PROJECT" --format="get(networkInterfaces[0].network)" | awk -F/ '{print $NF}')
  GCP_INSTANCE_TAG="turn-server"
  attempt "gcloud add instance tag" gcloud compute instances add-tags "$INSTANCE_NAME" --zone="$ZONE" --tags="$GCP_INSTANCE_TAG" --project="$VERTEX_PROJECT"
  if gcloud compute firewall-rules describe allow-turn --project="$VERTEX_PROJECT" >/dev/null 2>&1; then
    log "gcloud firewall allow-turn already exists; skipping"
  else
    attempt "gcloud create firewall allow-turn" gcloud compute firewall-rules create allow-turn --project="$VERTEX_PROJECT" --network="$GCP_NETWORK" --direction=INGRESS --priority=1000 --action=ALLOW --rules=tcp:3478,tcp:5349,udp:3478,udp:49160-49200 --source-ranges=0.0.0.0/0 --target-tags="$GCP_INSTANCE_TAG" --description="Allow TURN server ports"
  fi
  if gcloud compute firewall-rules describe allow-web --project="$VERTEX_PROJECT" >/dev/null 2>&1; then
    log "gcloud firewall allow-web already exists; skipping"
  else
    attempt "gcloud create firewall allow-web" gcloud compute firewall-rules create allow-web --project="$VERTEX_PROJECT" --network="$GCP_NETWORK" --direction=INGRESS --priority=1000 --action=ALLOW --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags="$GCP_INSTANCE_TAG" --description="Allow HTTP and HTTPS"
  fi
fi

PUBLIC_IP=$(curl -s ifconfig.me || true)
PRIVATE_IP=$(hostname -I | awk '{print $1}')
TURN_USER=${INPUT_TURN_USER:-webrtc}
TURN_PASS=${INPUT_TURN_PASS:-strongpassword}

# --- ensure TLS certs exist: try Let's Encrypt, fallback to self-signed ---
ensure_certs() {
  log "Ensuring TLS certs for ${DOMAIN} at ${CERT_DIR}"

  sudo mkdir -p "${CERT_DIR}"
  attempt "chown cert parent dir" sudo chown root:root "$(dirname "${CERT_DIR}")"

  create_self_signed() {
    log "Creating self-signed cert for ${DOMAIN} (temporary fallback)"
    sudo mkdir -p "${CERT_DIR}"
    sudo openssl req -x509 -nodes -newkey rsa:2048       -days 365       -subj "/CN=${DOMAIN}"       -keyout "${CERT_DIR}/privkey.pem"       -out "${CERT_DIR}/fullchain.pem" >/dev/null 2>&1 || {
        err "Failed to create self-signed cert"
        return 1
      }
    attempt "chmod fullchain.pem" sudo chmod 644 "${CERT_DIR}/fullchain.pem"
    attempt "chmod privkey.pem" sudo chmod 600 "${CERT_DIR}/privkey.pem"
    log "Self-signed cert created at ${CERT_DIR}"
    return 0
  }

  if command -v host >/dev/null 2>&1 && host "${DOMAIN}" >/dev/null 2>&1; then
    log "Domain ${DOMAIN} resolves, attempting Let's Encrypt via certbot (standalone)"
    attempt "ufw allow 80/tcp" sudo ufw allow 80/tcp
    attempt "ufw allow 443/tcp" sudo ufw allow 443/tcp

    NGINX_WAS_RUNNING=false
    if systemctl is-active --quiet nginx 2>/dev/null; then
      log "Temporarily stopping nginx for ACME challenge"
      attempt "stop nginx for certbot" sudo systemctl stop nginx
      NGINX_WAS_RUNNING=true
    fi

    if sudo certbot certonly --standalone         --non-interactive         --agree-tos         --email "admin@${DOMAIN}"         -d "${DOMAIN}"; then
      log "Let's Encrypt cert obtained for ${DOMAIN}"
    else
      err "certbot failed; falling back to self-signed certificate"
      create_self_signed || true
    fi

    if [ "$NGINX_WAS_RUNNING" = true ]; then
      log "Restarting nginx"
      attempt "restart nginx after certbot" sudo systemctl start nginx
    fi

  else
    log "Domain ${DOMAIN} does not appear to resolve. Creating self-signed cert."
    create_self_signed || true
  fi

  if [ ! -f "${CERT_DIR}/fullchain.pem" ] || [ ! -f "${CERT_DIR}/privkey.pem" ]; then
    err "TLS files missing at ${CERT_DIR}; creating self-signed cert as fallback"
    create_self_signed || true
  fi
}

ensure_certs

sudo tee /etc/turnserver.conf > /dev/null <<EOF
listening-port=3478
tls-listening-port=5349
listening-ip=0.0.0.0
fingerprint
lt-cred-mech
realm=${DOMAIN}
server-name=${DOMAIN}
user=${TURN_USER}:${TURN_PASS}
external-ip=${PUBLIC_IP}/${PRIVATE_IP}
cert=/etc/letsencrypt/live/${DOMAIN}/fullchain.pem
pkey=/etc/letsencrypt/live/${DOMAIN}/privkey.pem
min-port=49160
max-port=49200
no-multicast-peers
no-cli
stale-nonce
verbose
EOF
attempt "enable coturn in defaults" sudo sed -i 's/#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
attempt "systemctl enable coturn" sudo systemctl enable coturn
attempt "systemctl restart coturn" sudo systemctl restart coturn

# -------------------------
# Write app files
# -------------------------

write_file "main.py" <<'EOF_MAIN_PY'
from dotenv import load_dotenv
load_dotenv()

from quart import Quart
import socketio

from server.routes import register_routes
from server.socket_handlers import register_socket_handlers
from server.state import state


def create_app():
    app = Quart(__name__, static_folder="static", static_url_path="/static")
    sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*", transports=["websocket"], max_http_buffer_size=20000000)
    state.sio = sio
    register_routes(app)
    register_socket_handlers(sio)
    return socketio.ASGIApp(sio, other_asgi_app=app)


asgi_app = create_app()
EOF_MAIN_PY




write_file "requirements.txt" <<'EOF_REQUIREMENTS_TXT'
quart
python-socketio
aiortc
python-dotenv
hypercorn
google-genai
google-cloud-texttospeech
google-cloud-speech
numpy
EOF_REQUIREMENTS_TXT




write_file "server/__init__.py" <<'EOF_SERVER___INIT___PY'
# server package
EOF_SERVER___INIT___PY




write_file "server/state.py" <<'EOF_SERVER_STATE_PY'
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on", "y"}


@dataclass
class BroadcastSession:
    sid: str
    pc: Any
    tracks: Dict[str, Any] = field(default_factory=dict)
    ai_enabled: bool = True
    stt_enabled: bool = True
    ai_task_handles: List[Any] = field(default_factory=list)


class State:
    def __init__(self):
        self.sio = None
        self.broadcaster: Optional[BroadcastSession] = None
        self.viewers: Dict[str, Any] = {}
        self.viewer_ai_opt_in: Dict[str, bool] = {}
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        self.last_ai_chat_at: Dict[str, float] = {}
        self.max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
        self.ai_rate_limit_seconds = float(os.getenv("AI_RATE_LIMIT_SECONDS", "3"))
        self.ai_fallback = env_bool("AI_FALLBACK", False) or os.getenv("AI_FALLBACK_MODE", "").lower() == "mock"
        self.ai_mode = os.getenv("AI_MODE", "active").lower()  # idle|active
        if self.ai_mode not in {"idle", "active"}:
            self.ai_mode = "active"
        self.auto_moderation_kick = env_bool("AI_AUTOMOD_KICK", False)
        self.ai_engine = None

    def can_ai_chat(self, sid: str) -> bool:
        now = time.time()
        last = self.last_ai_chat_at.get(sid, 0)
        if now - last < self.ai_rate_limit_seconds:
            return False
        self.last_ai_chat_at[sid] = now
        return True


state = State()



def ensure_ai_engine():
    if state.ai_engine is None:
        from server.ai_engine import AIEngine
        state.ai_engine = AIEngine(state)
    return state.ai_engine
EOF_SERVER_STATE_PY




write_file "server/ai.py" <<'EOF_SERVER_AI_PY'
# server/ai.py
import asyncio
import io
import logging
import os
import subprocess
import time
import wave
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("server.ai")

# Try imports for production; if not available, we'll fallback to mocks.
try:
    from google import genai
except Exception:
    genai = None

try:
    from google.cloud import texttospeech
except Exception:
    texttospeech = None

try:
    from google.cloud import speech_v1 as speech
except Exception:
    speech = None

# configuration via env
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
AI_FALLBACK_MODE = os.getenv("AI_FALLBACK", "false").lower() in ("1", "true", "yes", "y") or os.getenv("AI_FALLBACK_MODE", "") == "mock"
STATIC_UPLOADS = Path("static/uploads")
STATIC_UPLOADS.mkdir(parents=True, exist_ok=True)


async def ai_chat(prompt: str, conversation_id: Optional[str] = None) -> str:
    """Return a generated text reply. Uses Vertex/GenAI when configured."""
    prompt = (prompt or "").strip()
    if not prompt:
        return "Please provide text."

    api_key = os.getenv("GOOGLE_API_KEY")
    if genai and not AI_FALLBACK_MODE:
        try:
            def _extract_text(resp) -> str:
                if not resp:
                    return ""
                txt = getattr(resp, "text", None)
                if txt:
                    return str(txt).strip()
                candidates = getattr(resp, "candidates", None) or []
                for cand in candidates:
                    content = getattr(cand, "content", None)
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        ptxt = getattr(part, "text", None)
                        if ptxt:
                            return str(ptxt).strip()
                return ""

            def genai_call():
                project = os.getenv("VERTEX_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
                location = os.getenv("VERTEX_LOCATION", "us-central1")
                model_name = os.getenv("VERTEX_MODEL", os.getenv("VERTEX_MODEL_NAME", "gemini-2.5-flash"))

                if api_key:
                    client = genai.Client(api_key=api_key)
                else:
                    client = genai.Client(vertexai=True, project=project, location=location)

                if hasattr(client, "models") and hasattr(client.models, "generate_content"):
                    resp = client.models.generate_content(model=model_name, contents=prompt)
                elif hasattr(client, "generate_content"):
                    resp = client.generate_content(model=model_name, contents=prompt)
                else:
                    return ""
                return _extract_text(resp)

            text = await asyncio.to_thread(genai_call)
            if text:
                return text
        except Exception:
            log.exception("genai ai_chat failed")

    return "AI is unavailable right now. Check genai library install, IAM role roles/aiplatform.user, and VERTEX_PROJECT/GOOGLE_CLOUD_PROJECT + VERTEX_LOCATION."


def _silence_wav(duration_s: float = 1.0, sr: int = 16000) -> bytes:
    frames = int(duration_s * sr)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for _ in range(frames):
            wf.writeframes((0).to_bytes(2, "little", signed=True))
    return buf.getvalue()


async def synthesize_text(text: str) -> tuple[str, bytes, str]:
    """
    Synthesize text to audio bytes. Returns (mime, audio_bytes, relative_url_if_saved).
    Saves a file under static/uploads and returns a url like /static/uploads/...
    """
    text = (text or "").strip()
    if not text:
        return "audio/wav", _silence_wav(0.2), ""

    # If texttospeech client available, use ADC/service account context
    if texttospeech and not AI_FALLBACK_MODE:
        try:
            def tts_call():
                client = texttospeech.TextToSpeechClient()
                synthesis_input = texttospeech.SynthesisInput(text=text)
                voice = texttospeech.VoiceSelectionParams(language_code="en-US", ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL)
                audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16)
                response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
                return response.audio_content

            audio_bytes = await asyncio.to_thread(tts_call)
            mime = "audio/wav"
            # save to uploads so clients can fetch via HTTP
            filename = f"tts_{int(time.time()*1000)}.wav"
            path = STATIC_UPLOADS / filename
            path.write_bytes(audio_bytes)
            return mime, audio_bytes, f"/static/uploads/{filename}"
        except Exception:
            log.exception("TTS failed, falling back to silence")

    # fallback: silence or tiny beep
    audio_bytes = _silence_wav(0.8)
    filename = f"tts_{int(time.time()*1000)}_fallback.wav"
    path = STATIC_UPLOADS / filename
    path.write_bytes(audio_bytes)
    return "audio/wav", audio_bytes, f"/static/uploads/{filename}"


async def _recognize_with_google(raw_pcm: bytes, sample_rate_hz: int = 16000) -> str:
    """Synchronous helper wrapped in thread to call google speech-to-text for a short chunk."""
    if not speech:
        return ""
    try:
        def sync_call():
            client = speech.SpeechClient()
            audio = speech.RecognitionAudio(content=raw_pcm)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate_hz,
                language_code="en-US",
                enable_automatic_punctuation=True,
            )
            resp = client.recognize(config=config, audio=audio)
            # concatenate results
            texts = []
            for res in resp.results:
                if res.alternatives:
                    texts.append(res.alternatives[0].transcript)
            return " ".join(texts)

        return await asyncio.to_thread(sync_call)
    except Exception:
        log.exception("google speech recognize failed")
        return ""


def _streaming_recognize_with_google(raw_pcm: bytes, sample_rate_hz: int = 16000) -> str:
    """Blocking helper: perform gRPC streaming recognize for a short PCM chunk."""
    if not speech or not raw_pcm:
        return ""

    client = speech.SpeechClient()
    recog_cfg = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate_hz,
        language_code="en-US",
        enable_automatic_punctuation=True,
    )
    stream_cfg = speech.StreamingRecognitionConfig(
        config=recog_cfg,
        interim_results=True,
        single_utterance=False,
    )

    chunk_size = 4096
    chunks = [raw_pcm[i:i + chunk_size] for i in range(0, len(raw_pcm), chunk_size)]

    def _requests():
        yield speech.StreamingRecognizeRequest(streaming_config=stream_cfg)
        for c in chunks:
            yield speech.StreamingRecognizeRequest(audio_content=c)

    responses = client.streaming_recognize(requests=_requests())
    texts = []
    for resp in responses:
        for result in resp.results:
            if result.alternatives:
                texts.append(result.alternatives[0].transcript)

    return " ".join([t.strip() for t in texts if t and t.strip()]).strip()


async def transcribe_pcm_google(raw_pcm: bytes, sample_rate_hz: int = 16000) -> str:
    return await _recognize_with_google(raw_pcm, sample_rate_hz)


async def transcribe_webm_opus(webm_bytes: bytes) -> str:
    if not webm_bytes:
        return ""

    try:
        def decode_to_pcm16k():
            p = subprocess.Popen(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-i", "pipe:0",
                    "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, _err = p.communicate(input=webm_bytes, timeout=10)
            return out

        pcm = await asyncio.to_thread(decode_to_pcm16k)
        if not pcm:
            return ""
        return await transcribe_pcm_google(pcm, sample_rate_hz=16000)
    except Exception:
        log.exception("[AI][STT] transcribe_webm_opus failed")
        return ""


async def transcribe_track(track, session_id: str, on_transcript_callback: Callable[[dict], asyncio.Future], stop_event: asyncio.Event):
    """
    Read audio frames from aiortc track, buffer into short chunks and either:
      - call Google Speech API (if configured).
    Call on_transcript_callback(payload) to emit transcripts (payload: dict with sid, text, final, ts)
    """
    # Short accumulator strategy: collect ~1.4s of audio then send recognition request
    buffer_frames = []
    total_frames = 0
    last_emit = time.time()
    sample_rate = 16000
    chunk_seconds = 1.4
    max_frames_before_emit = int(chunk_seconds * sample_rate)

    if AI_FALLBACK_MODE or not speech:
        await on_transcript_callback({
            "sid": session_id,
            "text": "[STT unavailable] Speech client not initialized. Check google-cloud-speech install, IAM roles (roles/speech.client), and AI_FALLBACK=false.",
            "final": True,
            "ts": int(time.time() * 1000),
        })
        return

    log.info("STT loop starting for %s", session_id)
    while not stop_event.is_set():
        try:
            frame = await asyncio.wait_for(track.recv(), timeout=3.0)
            # aiortc AudioFrame has to_ndarray()
            try:
                arr = frame.to_ndarray()
            except Exception:
                # fallback: can't convert
                arr = None

            if arr is None:
                total_frames += 1
            else:
                import numpy as np
                frame_rate = int(getattr(frame, "sample_rate", 16000) or 16000)
                # arr shape could be (channels, samples) or (samples, channels)
                if arr.ndim > 1:
                    if arr.shape[0] <= 2 and arr.shape[1] > arr.shape[0]:
                        arr_mono = arr[0, :]
                    else:
                        arr_mono = arr[:, 0]
                else:
                    arr_mono = arr

                if arr_mono.dtype in (np.float32, np.float64):
                    arr_f = np.clip(arr_mono, -1.0, 1.0).astype(np.float32)
                else:
                    arr_f = (arr_mono.astype(np.float32) / 32768.0)

                if frame_rate != 16000 and len(arr_f) > 1:
                    duration = len(arr_f) / float(frame_rate)
                    out_len = max(1, int(duration * 16000))
                    src_x = np.linspace(0.0, 1.0, len(arr_f), endpoint=False)
                    dst_x = np.linspace(0.0, 1.0, out_len, endpoint=False)
                    arr_f = np.interp(dst_x, src_x, arr_f).astype(np.float32)

                pcm = (np.clip(arr_f, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                buffer_frames.append(pcm)
                total_frames += len(arr_f)

            # choose to emit every ~chunk_seconds or if we haven't emitted for longer than 2s
            now = time.time()
            if (len(buffer_frames) > 0 and (now - last_emit) >= chunk_seconds) or (total_frames >= max_frames_before_emit):
                raw = b"".join(buffer_frames)
                buffer_frames = []
                total_frames = 0
                last_emit = now

                try:
                    # Prefer streaming gRPC recognition for lower latency behavior.
                    recognized = await asyncio.to_thread(_streaming_recognize_with_google, raw, sample_rate)
                    if not recognized:
                        recognized = await _recognize_with_google(raw, sample_rate)
                    if recognized:
                        await on_transcript_callback({
                            "sid": session_id,
                            "text": recognized,
                            "final": False,
                            "ts": int(time.time() * 1000),
                        })
                except Exception:
                    log.exception("google recognition failed")

        except asyncio.TimeoutError:
            # no frames for a while - emit keepalive or continue loop
            continue
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("STT frame loop ended for %s", session_id)
            break

    # send final message
    try:
        await on_transcript_callback({
            "sid": session_id,
            "text": "[STT] stopped",
            "final": True,
            "ts": int(time.time() * 1000),
        })
    except Exception:
        log.exception("failed to emit final STT message for %s", session_id)

    log.info("STT loop finished for %s", session_id)
EOF_SERVER_AI_PY




write_file "server/ai_engine.py" <<'EOF_SERVER_AI_ENGINE_PY'
import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol
from urllib.parse import quote_plus
from urllib.request import urlopen

from server import ai, webrtc

log = logging.getLogger("server.ai_engine")


class AICapability(Protocol):
    name: str

    async def handle(self, message: "AIMessage", ctx: "AIContext") -> Optional["AIResult"]:
        ...


class WebSearchProvider(Protocol):
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, str]]:
        ...


class DuckDuckGoWebSearchProvider:
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, str]]:
        if not query.strip():
            return []

        def _fetch() -> List[Dict[str, str]]:
            url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_redirect=1&no_html=1"
            with urlopen(url, timeout=8) as resp:  # nosec B310 - controlled URL
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            results: List[Dict[str, str]] = []
            related = data.get("RelatedTopics", [])
            for item in related:
                if isinstance(item, dict) and item.get("Text"):
                    results.append({
                        "title": item.get("Text", "")[:120],
                        "url": item.get("FirstURL", ""),
                        "snippet": item.get("Text", ""),
                    })
                if len(results) >= limit:
                    break
            return results

        try:
            return await asyncio.to_thread(_fetch)
        except Exception:
            log.exception("web search failed")
            return []


class NullWebSearchProvider:
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, str]]:
        return []


@dataclass
class AIMessage:
    sid: str
    role: str
    sender: str
    text: str
    ts: int
    message_id: str


@dataclass
class AIResult:
    text: Optional[str] = None
    command: Optional[Dict[str, Any]] = None
    moderation: Optional[Dict[str, Any]] = None
    web_results: Optional[List[Dict[str, str]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationState:
    history: Deque[Dict[str, str]] = field(default_factory=lambda: deque(maxlen=30))
    last_seen_ids: Deque[str] = field(default_factory=lambda: deque(maxlen=120))


@dataclass
class AIContext:
    room_id: str
    role: str
    state: Any
    sio: Any
    system_prompt: str
    conversation: ConversationState
    web_search: WebSearchProvider


class ModerationCapability:
    name = "moderation"

    def __init__(self):
        self.banned_terms = [w.strip().lower() for w in os.getenv("AI_MOD_BANNED_WORDS", "").split(",") if w.strip()]
        self.spam_repeat = int(os.getenv("AI_MOD_SPAM_REPEAT", "3"))
        self.strictness = os.getenv("AI_MOD_STRICTNESS", "medium").lower()

    async def handle(self, message: AIMessage, ctx: AIContext) -> Optional[AIResult]:
        text = message.text.lower().strip()
        if not text:
            return None

        if any(term in text for term in self.banned_terms):
            severity = "high" if self.strictness in {"high", "strict"} else "medium"
            return AIResult(moderation={"type": "abuse", "severity": severity, "action": "warn"})

        recent_user_msgs = [m.get("content", "") for m in list(ctx.conversation.history)[-6:] if m.get("role") == "user"]
        if recent_user_msgs.count(text) >= self.spam_repeat:
            return AIResult(moderation={"type": "spam", "severity": "medium", "action": "warn"})

        return None


class BroadcastControlCapability:
    name = "broadcast_control"

    _COMMAND_PATTERN = re.compile(r"^\s*/(?P<cmd>[a-z_]+)(?:\s+(?P<args>.*))?$", re.IGNORECASE)

    async def handle(self, message: AIMessage, ctx: AIContext) -> Optional[AIResult]:
        match = self._COMMAND_PATTERN.match(message.text)
        if not match:
            return None

        cmd = match.group("cmd").lower()
        args = (match.group("args") or "").strip()

        if cmd in {"mute", "unmute", "stop_broadcast", "warn", "kick", "set_title", "overlay", "ai_idle", "ai_active"}:
            command = {"action": cmd, "args": args, "requested_by": message.sid}
            return AIResult(command=command)
        return None


class WebSearchCapability:
    name = "web_search"

    async def handle(self, message: AIMessage, ctx: AIContext) -> Optional[AIResult]:
        text = message.text.strip()
        if not text.lower().startswith("/web "):
            return None
        query = text[5:].strip()
        if not query:
            return AIResult(text="Provide a search query after /web.")
        results = await ctx.web_search.search(query, limit=5)
        if not results:
            return AIResult(text="Web search is unavailable right now.", web_results=[])

        summary = "; ".join([f"{r.get('title', '')}" for r in results[:3] if r.get("title")])
        if not summary:
            summary = "Found results, but no summary text available."
        return AIResult(text=f"Web summary: {summary}", web_results=results)


class ChatCapability:
    name = "chat"

    async def handle(self, message: AIMessage, ctx: AIContext) -> Optional[AIResult]:
        prompt = message.text.strip()
        if not prompt:
            return AIResult(text="Please provide text.")

        conversation_hint = "\n".join([f"{m['role']}: {m['content']}" for m in list(ctx.conversation.history)[-8:]])
        full_prompt = (
            f"{ctx.system_prompt}\n"
            f"Role={ctx.role}; room={ctx.room_id}.\n"
            f"Recent conversation:\n{conversation_hint}\n"
            f"User message: {prompt}\n"
            f"Return concise helpful reply."
        )
        reply = await ai.ai_chat(full_prompt, ctx.room_id)
        return AIResult(text=reply)


class AIEngine:
    def __init__(self, state: Any, web_search_provider: Optional[WebSearchProvider] = None):
        self.state = state
        self._locks: Dict[str, asyncio.Lock] = {}
        self._rooms: Dict[str, ConversationState] = {}
        self.system_prompt = os.getenv(
            "AI_SYSTEM_PROMPT",
            "You are Broadcast Copilot. Help run a live stream, moderate chat, and provide concise guidance.",
        )
        if web_search_provider is not None:
            self.web_search = web_search_provider
        else:
            self.web_search = DuckDuckGoWebSearchProvider() if os.getenv("WEB_SEARCH_ENABLED", "1") in {"1", "true", "yes"} else NullWebSearchProvider()

        self.capabilities: List[AICapability] = [
            ModerationCapability(),
            BroadcastControlCapability(),
            WebSearchCapability(),
            ChatCapability(),
        ]

    def _room_id_for_sid(self, sid: str) -> str:
        if self.state.broadcaster:
            return self.state.broadcaster.sid
        return sid

    def _role_for_sid(self, sid: str) -> str:
        if self.state.broadcaster and self.state.broadcaster.sid == sid:
            return "broadcaster"
        return "viewer"

    def _conversation(self, room_id: str) -> ConversationState:
        if room_id not in self._rooms:
            self._rooms[room_id] = ConversationState()
        return self._rooms[room_id]

    def _lock(self, room_id: str) -> asyncio.Lock:
        if room_id not in self._locks:
            self._locks[room_id] = asyncio.Lock()
        return self._locks[room_id]

    async def process_chat(self, sio: Any, sid: str, sender: str, text: str, message_id: Optional[str] = None) -> Optional[AIResult]:
        room_id = self._room_id_for_sid(sid)
        role = self._role_for_sid(sid)
        convo = self._conversation(room_id)
        msg_id = message_id or f"{sid}:{int(time.time() * 1000)}:{hash(text)}"

        async with self._lock(room_id):
            if msg_id in convo.last_seen_ids:
                return None
            convo.last_seen_ids.append(msg_id)

            incoming = AIMessage(
                sid=sid,
                role=role,
                sender=sender or role,
                text=(text or "").strip(),
                ts=int(time.time() * 1000),
                message_id=msg_id,
            )
            convo.history.append({"role": "user", "content": incoming.text, "sender": incoming.sender})

            ctx = AIContext(
                room_id=room_id,
                role=role,
                state=self.state,
                sio=sio,
                system_prompt=self.system_prompt,
                conversation=convo,
                web_search=self.web_search,
            )

            for capability in self.capabilities:
                result = await capability.handle(incoming, ctx)
                if result:
                    if result.text:
                        convo.history.append({"role": "assistant", "content": result.text, "sender": "ai"})
                    return result

        return None

    async def execute_command(self, sio: Any, command: Dict[str, Any], origin_sid: str) -> Dict[str, Any]:
        action = (command or {}).get("action", "").lower()
        args = (command or {}).get("args", "")
        role = self._role_for_sid(origin_sid)

        if role != "broadcaster":
            return {"ok": False, "error": "forbidden"}

        if action == "mute":
            await sio.emit("control_action", {"action": "mute_broadcaster", "reason": args})
            return {"ok": True}
        if action == "unmute":
            await sio.emit("control_action", {"action": "unmute_broadcaster", "reason": args})
            return {"ok": True}
        if action == "stop_broadcast":
            await webrtc.stop_broadcaster(origin_sid)
            await sio.emit("stream_stopped", {"reason": args or "stopped by broadcaster"})
            return {"ok": True}
        if action == "warn":
            await sio.emit("moderation_event", {"action": "warn", "message": args})
            return {"ok": True}
        if action == "kick":
            target_sid = args.strip()
            if target_sid:
                await webrtc.stop_viewer(target_sid)
                await sio.emit("moderation_event", {"action": "kick", "viewer_sid": target_sid})
                return {"ok": True}
            return {"ok": False, "error": "missing_target"}
        if action == "set_title":
            await sio.emit("control_action", {"action": "set_title", "value": args})
            return {"ok": True}
        if action == "overlay":
            await sio.emit("control_action", {"action": "overlay", "value": args})
            return {"ok": True}
        if action == "ai_idle":
            self.state.ai_mode = "idle"
            await sio.emit("ai_status", {"enabled": True, "mode": self.state.ai_mode})
            return {"ok": True}
        if action == "ai_active":
            self.state.ai_mode = "active"
            await sio.emit("ai_status", {"enabled": True, "mode": self.state.ai_mode})
            return {"ok": True}

        return {"ok": False, "error": "unsupported_action"}
EOF_SERVER_AI_ENGINE_PY




write_file "server/webrtc.py" <<'EOF_SERVER_WEBRTC_PY'
# server/webrtc.py
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.sdp import candidate_to_sdp

from server import ai
from server.state import BroadcastSession, state

log = logging.getLogger("server.webrtc")
relay = MediaRelay()


@dataclass
class STTHandle:
    stop_event: asyncio.Event
    task: asyncio.Task


_stt_handles: Dict[str, STTHandle] = {}


def _ice_to_dict(candidate) -> dict:
    if candidate is None:
        return {"candidate": None}
    return {
        "candidate": f"candidate:{candidate_to_sdp(candidate)}",
        "sdpMid": getattr(candidate, "sdpMid", None),
        "sdpMLineIndex": getattr(candidate, "sdpMLineIndex", None),
    }


async def _emit_ai_transcript(payload: dict):
    if not state.sio:
        return
    try:
        await state.sio.emit("ai_transcript", payload)
        await state.sio.emit(
            "chat_message",
            {
                "sender": "stt",
                "text": payload.get("text", ""),
                "ts": payload.get("ts"),
            },
        )
    except Exception:
        log.exception("emit ai_transcript failed")


async def _start_stt_if_possible(sid: str):
    if not state.broadcaster or state.broadcaster.sid != sid:
        return
    if not state.broadcaster.ai_enabled or not state.broadcaster.stt_enabled:
        return
    if sid in _stt_handles:
        return

    audio_track = state.broadcaster.tracks.get("audio")
    if not audio_track:
        return

    relayed = relay.subscribe(audio_track)
    stop_event = asyncio.Event()
    task = asyncio.create_task(ai.transcribe_track(relayed, sid, _emit_ai_transcript, stop_event))
    _stt_handles[sid] = STTHandle(stop_event=stop_event, task=task)
    log.info("started STT task for broadcaster %s", sid)


async def _wait_for_ice_gathering_complete(pc: RTCPeerConnection, timeout_s: float = 5.0):
    if pc.iceGatheringState == "complete":
        return
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if pc.iceGatheringState == "complete":
            return
        await asyncio.sleep(0.05)


async def start_broadcaster(sid: str, sdp: str, sdp_type: str):
    if state.broadcaster:
        await stop_broadcaster(state.broadcaster.sid)

    pc = RTCPeerConnection()
    sess = BroadcastSession(sid=sid, pc=pc)
    state.broadcaster = sess

    @pc.on("track")
    def on_track(track):
        sess.tracks[track.kind] = track
        log.info("broadcaster %s track available: %s", sid, track.kind)
        if track.kind == "audio":
            asyncio.create_task(_start_stt_if_possible(sid))

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        if state.sio:
            await state.sio.emit("webrtc_ice_server", _ice_to_dict(candidate), to=sid)

    @pc.on("connectionstatechange")
    async def on_state_change():
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await stop_broadcaster(sid)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    await _wait_for_ice_gathering_complete(pc)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def stop_broadcaster(sid: str):
    if not state.broadcaster or state.broadcaster.sid != sid:
        return
    await disable_ai_for_broadcaster(sid)
    for viewer_sid in list(state.viewers.keys()):
        await stop_viewer(viewer_sid)
    try:
        await state.broadcaster.pc.close()
    except Exception:
        log.exception("error closing broadcaster pc")
    state.broadcaster = None


async def _wait_for_broadcaster_tracks(timeout_s: float = 8.0):
    if not state.broadcaster:
        return
    if state.broadcaster.tracks.get("audio") or state.broadcaster.tracks.get("video"):
        return

    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if not state.broadcaster:
            return
        if state.broadcaster.tracks.get("audio") or state.broadcaster.tracks.get("video"):
            return
        await asyncio.sleep(0.05)


async def start_viewer(sid: str, sdp: str, sdp_type: str):
    if not state.broadcaster:
        raise RuntimeError("No active broadcaster")

    await _wait_for_broadcaster_tracks()

    pc = RTCPeerConnection()
    state.viewers[sid] = pc

    if not state.broadcaster or state.broadcaster.sid is None:
        raise RuntimeError("No active broadcaster")

    added = 0
    for kind in ("video", "audio"):
        track = state.broadcaster.tracks.get(kind)
        if track:
            pc.addTrack(relay.subscribe(track))
            added += 1

    if added == 0:
        log.warning("viewer %s connected before broadcaster media became available", sid)
        await pc.close()
        state.viewers.pop(sid, None)
        raise RuntimeError("Broadcaster media not ready yet. Retry /watch in a moment.")

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        if state.sio:
            await state.sio.emit("watch_ice_server", _ice_to_dict(candidate), to=sid)

    @pc.on("connectionstatechange")
    async def on_state_change():
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await stop_viewer(sid)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    await _wait_for_ice_gathering_complete(pc)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def add_broadcaster_ice_candidate(sid: str, candidate: Optional[object]):
    if not state.broadcaster or state.broadcaster.sid != sid:
        return
    await state.broadcaster.pc.addIceCandidate(candidate)


async def add_viewer_ice_candidate(sid: str, candidate: Optional[object]):
    pc = state.viewers.get(sid)
    if not pc:
        return
    await pc.addIceCandidate(candidate)


async def stop_viewer(sid: str):
    pc = state.viewers.pop(sid, None)
    if pc:
        try:
            await pc.close()
        except Exception:
            log.exception("error closing viewer pc")


async def enable_ai_for_broadcaster(sid: str):
    if not state.broadcaster or state.broadcaster.sid != sid:
        return
    state.broadcaster.ai_enabled = True
    state.broadcaster.stt_enabled = True
    await _start_stt_if_possible(sid)


async def disable_ai_for_broadcaster(sid: str):
    if state.broadcaster and state.broadcaster.sid == sid:
        state.broadcaster.ai_enabled = False
        state.broadcaster.stt_enabled = False

    handle = _stt_handles.pop(sid, None)
    if not handle:
        return

    handle.stop_event.set()
    handle.task.cancel()
    try:
        await handle.task
    except Exception:
        pass
EOF_SERVER_WEBRTC_PY




write_file "server/socket_handlers.py" <<'EOF_SERVER_SOCKET_HANDLERS_PY'
# server/socket_handlers.py
import asyncio
import base64
import logging
import os
import re
import time
from pathlib import Path

from aiortc.sdp import candidate_from_sdp

from server import ai, webrtc
from server.state import ensure_ai_engine, state

UPLOAD_DIR = Path("static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("server.socket_handlers")

_stt_buffers = {}
_stt_last = {}


def _safe_name(name: str) -> str:
    name = (name or "file.bin").strip()
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return name[:120] or "file.bin"


def _parse_ice_candidate(data):
    cand = (data or {}).get("candidate", data)
    if cand is None:
        return None

    if isinstance(cand, dict):
        candidate_sdp = cand.get("candidate")
        sdp_mid = cand.get("sdpMid")
        sdp_mline_index = cand.get("sdpMLineIndex")
    else:
        candidate_sdp = cand
        sdp_mid = (data or {}).get("sdpMid")
        sdp_mline_index = (data or {}).get("sdpMLineIndex")

    if not candidate_sdp:
        return None
    if isinstance(candidate_sdp, str) and candidate_sdp.startswith("candidate:"):
        candidate_sdp = candidate_sdp.split(":", 1)[1]

    ice = candidate_from_sdp(candidate_sdp)
    ice.sdpMid = sdp_mid
    ice.sdpMLineIndex = sdp_mline_index
    return ice


async def _emit_ai_status(sio):
    await sio.emit(
        "ai_status",
        {
            "enabled": bool(state.broadcaster and state.broadcaster.ai_enabled),
            "mode": state.ai_mode,
        },
    )


async def _maybe_emit_ai_result(sio, sid: str, result):
    if not result:
        return

    if result.moderation:
        moderation_payload = {
            "sid": sid,
            "ts": int(time.time() * 1000),
            **result.moderation,
        }
        await sio.emit("moderation_event", moderation_payload)
        if state.auto_moderation_kick and result.moderation.get("action") == "warn":
            await sio.emit("moderation_event", {"action": "escalate", "sid": sid})

    if result.command:
        command_result = await ensure_ai_engine().execute_command(sio, result.command, sid)
        await sio.emit("ai_command_result", {"sid": sid, "command": result.command, "result": command_result})

    if result.web_results is not None:
        await sio.emit("ai_web_results", {"sid": sid, "results": result.web_results, "ts": int(time.time() * 1000)})

    if result.text:
        payload = {
            "conversation_id": sid,
            "text": result.text,
            "sender": "ai",
            "ts": int(time.time() * 1000),
            "kind": "structured",
        }
        await sio.emit("ai_chat_reply", payload)
        await sio.emit("chat_message", payload)


def register_socket_handlers(sio):
    @sio.event
    async def connect(sid, environ):
        ensure_ai_engine()
        await sio.emit(
            "ai_status",
            {
                "enabled": bool(state.broadcaster and state.broadcaster.ai_enabled),
                "mode": state.ai_mode,
            },
            to=sid,
        )

    @sio.event
    async def disconnect(sid):
        if state.broadcaster and state.broadcaster.sid == sid:
            await webrtc.stop_broadcaster(sid)
            await sio.emit("stream_stopped", {})
        else:
            await webrtc.stop_viewer(sid)

    @sio.on("webrtc_offer")
    async def webrtc_offer(sid, data):
        sdp = data.get("sdp") or data.get("offer")
        try:
            answer = await webrtc.start_broadcaster(sid, sdp, data.get("type", "offer"))
            await sio.emit("webrtc_answer", answer, to=sid)
            await sio.emit("stream_started", {})
            await webrtc.enable_ai_for_broadcaster(sid)
            await _emit_ai_status(sio)
        except Exception as e:
            await sio.emit("webrtc_error", {"error": str(e)}, to=sid)

    @sio.on("webrtc_ice")
    async def webrtc_ice(sid, data):
        try:
            candidate = _parse_ice_candidate(data)
            await webrtc.add_broadcaster_ice_candidate(sid, candidate)
        except Exception:
            log.exception("failed to add broadcaster ice candidate for %s", sid)

    @sio.on("watch_start")
    async def watch_start(sid, data):
        try:
            answer = await webrtc.start_viewer(sid, data["sdp"], data.get("type", "offer"))
            await sio.emit("watch_answer", answer, to=sid)
        except Exception as e:
            await sio.emit("watch_error", {"error": str(e)}, to=sid)

    @sio.on("watch_ice")
    async def watch_ice(sid, data):
        try:
            candidate = _parse_ice_candidate(data)
            await webrtc.add_viewer_ice_candidate(sid, candidate)
        except Exception:
            log.exception("failed to add viewer ice candidate for %s", sid)

    @sio.on("stt_chunk")
    async def stt_chunk(sid, data):
        ai_enabled = bool(state.broadcaster and state.broadcaster.ai_enabled)
        if not ai_enabled or state.ai_mode != "active":
            return

        b64 = (data or {}).get("b64")
        if not b64:
            return

        try:
            chunk = base64.b64decode(b64)
        except Exception:
            return

        buf = _stt_buffers.setdefault(sid, bytearray())
        buf.extend(chunk)

        now = time.time()
        last = _stt_last.get(sid, 0)
        if now - last < 1.2:
            return
        _stt_last[sid] = now

        raw = bytes(buf)
        buf.clear()

        text = await ai.transcribe_webm_opus(raw)
        if text and text.strip():
            payload = {
                "sender": "stt",
                "text": text.strip(),
                "final": False,
                "ts": int((data or {}).get("ts") or (time.time() * 1000)),
                "message_id": f"stt-{sid}-{int(now*1000)}",
            }
            await sio.emit("chat_message", payload)

    @sio.on("webrtc_stop")
    async def webrtc_stop(sid, _data):
        await webrtc.stop_broadcaster(sid)
        await sio.emit("stream_stopped", {})

    @sio.on("chat_message")
    async def chat_message(sid, data):
        payload = {
            "sender": data.get("sender", "user"),
            "text": data.get("text") or data.get("message", ""),
            "ts": int(time.time() * 1000),
            "message_id": data.get("message_id") or f"msg-{sid}-{int(time.time()*1000)}",
        }
        await sio.emit("chat_message", payload)

        try:
            ai_enabled = bool(state.broadcaster and state.broadcaster.ai_enabled)
            if ai_enabled and state.ai_mode == "active" and state.can_ai_chat(sid):
                result = await asyncio.wait_for(
                    ensure_ai_engine().process_chat(
                        sio=sio,
                        sid=sid,
                        sender=payload.get("sender", "user"),
                        text=payload.get("text", ""),
                        message_id=payload.get("message_id"),
                    ),
                    timeout=35,
                )
                await _maybe_emit_ai_result(sio, sid, result)
        except Exception:
            log.exception("AI chat failed")

    @sio.on("ai_enable")
    async def ai_enable(sid, _data=None):
        if state.broadcaster and state.broadcaster.sid == sid:
            await webrtc.enable_ai_for_broadcaster(sid)
            await sio.emit("ai_enabled", {"sid": sid})
            await _emit_ai_status(sio)

    @sio.on("ai_disable")
    async def ai_disable(sid, _data=None):
        if state.broadcaster and state.broadcaster.sid == sid:
            await webrtc.disable_ai_for_broadcaster(sid)
            await sio.emit("ai_disabled", {"sid": sid})
            await _emit_ai_status(sio)

    @sio.on("ai_mode_set")
    async def ai_mode_set(sid, data):
        mode = (data or {}).get("mode", "active").lower()
        if mode not in {"idle", "active"}:
            mode = "active"
        state.ai_mode = mode
        await _emit_ai_status(sio)

    @sio.on("ai_stt_enable")
    async def ai_stt_enable(sid, _data=None):
        await ai_enable(sid)

    @sio.on("ai_stt_disable")
    async def ai_stt_disable(sid, _data=None):
        await ai_disable(sid)

    @sio.on("ai_chat")
    async def ai_chat_event(sid, data):
        if not state.can_ai_chat(sid):
            await sio.emit("ai_error", {"error": "rate_limited"}, to=sid)
            return
        if state.ai_mode != "active":
            await sio.emit("ai_error", {"error": "ai_idle_mode"}, to=sid)
            return

        text = (data or {}).get("text", "")
        message_id = (data or {}).get("message_id")
        sender = (data or {}).get("sender", "user")

        try:
            result = await asyncio.wait_for(
                ensure_ai_engine().process_chat(sio=sio, sid=sid, sender=sender, text=text, message_id=message_id),
                timeout=35,
            )
            await _maybe_emit_ai_result(sio, sid, result)
        except asyncio.TimeoutError:
            await sio.emit("ai_error", {"error": "ai_timeout"}, to=sid)
        except Exception:
            log.exception("ai_chat failure")
            await sio.emit("ai_error", {"error": "ai_chat_failed"}, to=sid)

    @sio.on("ai_tts_request")
    async def ai_tts_request(sid, data):
        text = (data or {}).get("text", "")
        try:
            mime, _audio_bytes, rel_url = await asyncio.wait_for(ai.synthesize_text(text), timeout=30)
            await sio.emit("ai_tts_audio", {"url": rel_url, "mime": mime, "text": text})
        except Exception:
            await sio.emit("ai_error", {"error": "tts_failed"}, to=sid)

    @sio.on("upload_file")
    async def upload_file(sid, data):
        try:
            name = _safe_name((data or {}).get("name", "file.bin"))
            b64 = (data or {}).get("content_base64", "")
            raw = base64.b64decode(b64)
            max_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
            if len(raw) > max_bytes:
                await sio.emit("ai_error", {"error": "file_too_large"}, to=sid)
                return
            filename = f"{int(time.time()*1000)}_{name}"
            path = UPLOAD_DIR / filename
            path.write_bytes(raw)
            payload = {
                "sender": (data or {}).get("sender", "user"),
                "text": (data or {}).get("text", "uploaded a file"),
                "media_url": f"/static/uploads/{filename}",
                "media_type": (data or {}).get("mime", "application/octet-stream"),
                "ts": int(time.time() * 1000),
            }
            await sio.emit("chat_message", payload)
        except Exception:
            await sio.emit("ai_error", {"error": "upload_failed"}, to=sid)
EOF_SERVER_SOCKET_HANDLERS_PY




write_file "server/routes.py" <<'EOF_SERVER_ROUTES_PY'
import os
from quart import jsonify, request, send_from_directory, Response

from server import ai
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
<title>Broadcast — Broadcaster</title>
<link rel="stylesheet" href="/static/css_app.css">
<link rel="stylesheet" href="/static/css/main.css">
<script src="/static/socket.io.js"></script>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="brand"><h1>Broadcast</h1><div class="small-muted">Live studio</div></div>
    <div class="status-row">
      <div class="status-pill" title="Camera"><div id="camLight" class="status-light c-green status-glow blink-normal"></div><div class="status-label" id="camLabel">Cam: On</div></div>
      <div class="status-pill" title="Microphone"><div id="micLight" class="status-light c-green blink-normal"></div><div class="status-label" id="micLabel">Mic: On</div></div>
      <div class="status-pill" title="AI"><div id="aiLight" class="status-light c-green blink-normal status-glow"></div><div class="status-label" id="aiLabel">AI: On</div></div>
      <div class="status-pill" title="AI Mode"><div class="status-label">Mode</div>
        <select id="aiMode" class="btn" style="padding:6px 8px;">
          <option value="active" selected>active</option>
          <option value="idle">idle</option>
        </select>
      </div>
    </div>
  </div>

  <div class="main-grid">
    <div class="video-card">
      <div class="video-frame" style="position:relative;">
        <video id="previewVideo" autoplay playsinline muted></video>
        <canvas id="compositeCanvas" style="display:none; width:100%; height:100%; position:absolute; inset:0;"></canvas>
        <video id="screenSourceVideo" autoplay playsinline muted style="display:none;"></video>
        <video id="camSourceVideo" autoplay playsinline muted style="display:none;"></video>
      </div>

      <div class="card-grid">
        <div class="info-card"><div>Connection</div><div id="connBadge" class="badge connecting">CONNECTING</div></div>
        <div class="info-card"><div class="stat-line">ICE: <span id="iceState">new</span></div><div class="stat-line">PC: <span id="pcState">new</span></div></div>
        <div class="info-card"><div class="stat-line">Bitrate: <span id="bitrate">0 kbps</span></div><div class="stat-line">Packet Loss: <span id="packetLoss">0</span></div></div>
      </div>
      <div class="controls">
        <select id="videoDevices" class="btn"></select>
        <select id="camResolution" class="btn">
          <option value="1080p">1080p</option>
          <option value="2k" selected>2k</option>
          <option value="4k">4k</option>
        </select>
        <button id="toggleCam" class="btn">Switch Camera</button>
        <button id="shareScreen" class="btn">Share Screen</button>
        <button id="micToggle" class="btn">Mic: on</button>
        <button id="noiseToggle" class="btn">NoiseCancel: off</button>
        <button id="aiToggle" class="btn">AI on</button>
        <button id="recordToggle" class="btn">Record</button>
      </div>
    </div>

    <div class="chat-card">
      <div class="chat-list" id="chat"></div>
      <div class="controls">
        <button id="attachBtn" class="btn">Attach</button><input id="file" type="file" hidden>
        <button id="webBtn" class="btn">WebSearch</button>
        <button id="readBtn" class="btn">Read Aloud</button>
        <button id="wakeToggle" class="btn" style="display:none;">Wake Lock: on</button>
      </div>
      <div class="chat-input">
        <textarea id="chatInput" class="chat-text" placeholder="Message — Enter to send, Ctrl+Enter newline"></textarea>
        <button id="sendBtn" class="icon-btn">➡</button>
      </div>
    </div>
  </div>
</div>

<script>
const socket = io(window.location.origin, { path: "/socket.io", transports: ["websocket"], upgrade: false });
const camLight = document.getElementById('camLight');
const micLight = document.getElementById('micLight');
const aiLight = document.getElementById('aiLight');
const camLabel = document.getElementById('camLabel');
const micLabel = document.getElementById('micLabel');
const aiLabel = document.getElementById('aiLabel');
const aiModeSel = document.getElementById('aiMode');

const previewVideo = document.getElementById('previewVideo');
const compositeCanvas = document.getElementById('compositeCanvas');
const screenSourceVideo = document.getElementById('screenSourceVideo');
const camSourceVideo = document.getElementById('camSourceVideo');
const videoDevices = document.getElementById('videoDevices');
const resolutionSelect = document.getElementById('camResolution');
const chatList = document.getElementById('chat');
const chatInput = document.getElementById('chatInput');
const connBadge = document.getElementById('connBadge');
const iceStateEl = document.getElementById('iceState');
const pcStateEl = document.getElementById('pcState');
const bitrateEl = document.getElementById('bitrate');
const packetLossEl = document.getElementById('packetLoss');

let pc = null;
let camStream = null;
let screenStream = null;
let micStream = null;
let mediaRecorder = null;
let recordedChunks = [];
let currentVideoDeviceId = null;
let facingMode = 'user';
let aiOn = true;
let micEnabled = true;
let noiseCancellationEnabled = false;
let iceServers = [{urls:'stun:stun.l.google.com:19302'}];
let sttRecorder = null;
let sttEnabled = true; // always-on mic STT while broadcaster page is active
let ttsEnabled = false;
let ttsSeenIds = new Set();
let lastTtsRequestAt = 0;
let drawRaf = null;
let canvasStream = null;
let pipRect = { x: 20, y: 20, w: 240, h: 140 };
let pipDrag = null;
let isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');
let wakeLockEnabled = true;
let wakeLockSentinel = null;
let batteryLevel = 1;
let isBroadcasting = false;

function ttsMsgId(m){
  const base = `${m.message_id||''}|${m.sender||''}|${m.ts||''}|${m.text||''}`;
  let h = 0;
  for(let i=0;i<base.length;i++){ h = ((h<<5)-h) + base.charCodeAt(i); h |= 0; }
  return m.message_id || `h${Math.abs(h)}`;
}

function shouldRequestTts(m){
  if(!ttsEnabled) return false;
  const sender = (m.sender||'').toLowerCase();
  if(sender === 'system') return false;
  const text = String(m.text||'').trim();
  if(!text) return false;
  if(text.startsWith('[STT unavailable]')) return false;
  return true;
}

function maybeRequestTts(m){
  if(!shouldRequestTts(m)) return;
  const id = ttsMsgId(m);
  if(ttsSeenIds.has(id)) return;
  const now = Date.now();
  if(now - lastTtsRequestAt < 800) return;
  ttsSeenIds.add(id);
  lastTtsRequestAt = now;
  socket.emit('ai_tts_request', { text: String(m.text||'').trim() });
}

function addMsg(m){
  const el=document.createElement('div'); el.className='chat-entry';
  const time = new Date((m.ts||Date.now())).toLocaleTimeString();
  el.innerHTML=`<b>${m.sender||'user'}</b> <small>${time}</small><div>${escapeHtml(m.text||'')}</div>`;
  if(m.media_url){
    const mt=m.media_type||'';
    if(mt.startsWith('image')||m.media_url.match(/\.(png|jpg|jpeg|gif)$/i)) el.innerHTML += `<img class="msg-media" src="${m.media_url}">`;
    else if(mt.startsWith('audio')) el.innerHTML += `<audio controls src="${m.media_url}"></audio>`;
    else if(mt.startsWith('video')||m.media_url.match(/\.(webm|mp4|ogv)$/i)) el.innerHTML += `<video class="msg-media" controls src="${m.media_url}"></video>`;
    else el.innerHTML += `<a href="${m.media_url}" target="_blank">download</a>`;
  }
  chatList.appendChild(el); chatList.scrollTop = chatList.scrollHeight;
  maybeRequestTts(m);
}
function escapeHtml(s){ return (s||'').replace(/[&<"'>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }

function updateWakeButton(){
  const btn = document.getElementById('wakeToggle');
  if(!btn) return;
  if(!isMobile || !('wakeLock' in navigator)){ btn.style.display='none'; return; }
  btn.style.display='inline-block';
  btn.textContent = `Wake Lock: ${wakeLockEnabled ? 'on' : 'off'}`;
}

function shouldHoldWakeLock(){
  if(!isMobile || !wakeLockEnabled || !('wakeLock' in navigator)) return false;
  return batteryLevel < 0.15 ? isBroadcasting : true;
}

async function releaseWakeLock(){
  if(!wakeLockSentinel) return;
  try{ await wakeLockSentinel.release(); }catch(_){ }
  wakeLockSentinel = null;
}

async function applyWakeLockState(){
  if(!('wakeLock' in navigator)) return;
  if(!shouldHoldWakeLock()){
    await releaseWakeLock();
    return;
  }
  if(wakeLockSentinel) return;
  try{
    wakeLockSentinel = await navigator.wakeLock.request('screen');
    wakeLockSentinel.addEventListener('release', ()=>{ wakeLockSentinel = null; });
  }catch(e){
    console.warn('wake lock unavailable', e);
  }
}

async function initWakeLock(){
  updateWakeButton();
  if(!isMobile) return;
  if(navigator.getBattery){
    try{
      const battery = await navigator.getBattery();
      batteryLevel = typeof battery.level === 'number' ? battery.level : 1;
      const onBattery = ()=>{ batteryLevel = typeof battery.level === 'number' ? battery.level : batteryLevel; applyWakeLockState(); };
      battery.addEventListener('levelchange', onBattery);
      battery.addEventListener('chargingchange', onBattery);
    }catch(e){
      console.warn('battery api unavailable', e);
    }
  }
  document.addEventListener('visibilitychange', ()=>{
    if(document.visibilityState === 'visible') applyWakeLockState();
    else releaseWakeLock();
  });
  await applyWakeLockState();
}

function setStatusLight(lightEl,labelEl,{colorClass,label,blinkClass,glow}){ lightEl.classList.remove('c-green','c-red','c-yellow','c-orange','c-blue','blink-slow','blink-normal','blink-fast','status-glow'); if(colorClass) lightEl.classList.add(colorClass); if(blinkClass) lightEl.classList.add(blinkClass); if(glow) lightEl.classList.add('status-glow'); labelEl.textContent=label; }

function setConnBadge(state){
  connBadge.classList.remove('connecting','connected','failed');
  if(state === 'connected') { connBadge.classList.add('connected'); connBadge.textContent='CONNECTED'; return; }
  if(state === 'failed' || state === 'disconnected') { connBadge.classList.add('failed'); connBadge.textContent='FAILED'; return; }
  connBadge.classList.add('connecting'); connBadge.textContent='CONNECTING';
}

let lastBytes=0,lastStatsAt=0;
setInterval(async ()=>{
  if(!pc) return;
  try{
    const stats = await pc.getStats();
    let bytesNow=0, loss=0;
    stats.forEach(r=>{
      if(r.type==='outbound-rtp' && r.kind==='video'){ bytesNow += (r.bytesSent||0); loss += (r.packetsLost||0); }
      if(r.type==='candidate-pair' && r.state==='succeeded'){ iceStateEl.textContent='connected'; }
    });
    const now=Date.now();
    if(lastStatsAt){
      const br=Math.max(0,Math.round((bytesNow-lastBytes)*8/((now-lastStatsAt)/1000)/1000));
      bitrateEl.textContent=`${br} kbps`;
    }
    lastBytes=bytesNow; lastStatsAt=now; packetLossEl.textContent=String(loss);
  }catch(_){ }
},2000);

function sanitizeIceServers(raw){
  if(!Array.isArray(raw)) return [{urls:'stun:stun.l.google.com:19302'}];
  const cleaned = raw.map(entry => {
    if(!entry || !entry.urls) return null;
    const urls = Array.isArray(entry.urls) ? entry.urls : [entry.urls];
    const safe = urls.map(u => {
      if(typeof u !== 'string') return null;
      const s=u.trim();
      if(s.startsWith('stun:')||s.startsWith('turn:')||s.startsWith('turns:')) return s;
      return `turn:${s}:3478?transport=udp`;
    }).filter(Boolean);
    if(!safe.length) return null;
    return { urls: safe.length===1 ? safe[0] : safe, username: entry.username, credential: entry.credential };
  }).filter(Boolean);
  return cleaned.length ? cleaned : [{urls:'stun:stun.l.google.com:19302'}];
}

async function loadIceServers(){
  try{
    const r=await fetch('/webrtc/ice-config');
    if(!r.ok) return;
    const j=await r.json();
    if(j && Array.isArray(j.iceServers) && j.iceServers.length){
      iceServers=sanitizeIceServers(j.iceServers);
    }
  }catch(e){ console.warn('ice config fallback to stun only',e); }
}

function selectedVideoConstraints(deviceId){
  const preset = resolutionSelect.value || '2k';
  const presets = {
    '1080p': { width: { ideal: 1920 }, height: { ideal: 1080 } },
    '2k': { width: { ideal: 2560 }, height: { ideal: 1440 } },
    '4k': { width: { ideal: 3840 }, height: { ideal: 2160 } },
  };
  const base = presets[preset] || presets['2k'];
  if(deviceId){
    return { ...base, deviceId: { exact: deviceId } };
  }
  return { ...base, facingMode: { ideal: facingMode } };
}

function buildAudioConstraints(){
  return {
    echoCancellation: false,
    noiseSuppression: noiseCancellationEnabled,
    autoGainControl: false,
    channelCount: 1,
    sampleRate: 48000,
  };
}

async function enumerateVideoDevices(){
  try{
    const devices=await navigator.mediaDevices.enumerateDevices();
    const videos=devices.filter(d=>d.kind==='videoinput');
    videoDevices.innerHTML='';
    videos.forEach((d,i)=>{
      const o=document.createElement('option');
      o.value=d.deviceId;
      o.textContent=d.label||`camera ${i+1}`;
      videoDevices.appendChild(o);
    });
    if(videos.length){
      videoDevices.value=currentVideoDeviceId||videos[0].deviceId;
      currentVideoDeviceId=videoDevices.value;
    }
  }catch(e){ console.warn(e); }
}

async function startCam(deviceId){
  const constraints = { video: selectedVideoConstraints(deviceId), audio: false };
  const s = await navigator.mediaDevices.getUserMedia(constraints);
  if(camStream) camStream.getTracks().forEach(t=>t.stop());
  camStream = s;
  camSourceVideo.srcObject = camStream;
  previewVideo.srcObject = camStream;
  compositeCanvas.style.display='none';
  previewVideo.style.display='block';
  await enumerateVideoDevices();
}

async function startMic(){
  if(micStream) return;
  micStream = await navigator.mediaDevices.getUserMedia({ audio: buildAudioConstraints(), video: false });
  micStream.getAudioTracks().forEach(t => t.enabled = micEnabled);
  if(sttEnabled && !sttRecorder){
    setTimeout(() => { startSttMic().catch(()=>{}); }, 0);
  }
}

function stopMic(){
  if(!micStream) return;
  micStream.getTracks().forEach(t=>t.stop());
  micStream = null;
}

function abToB64(buf){
  let bin='';
  const bytes=new Uint8Array(buf);
  const chunk=0x8000;
  for(let i=0;i<bytes.length;i+=chunk){
    bin += String.fromCharCode.apply(null, bytes.subarray(i,i+chunk));
  }
  return btoa(bin);
}

async function startSttMic(){
  if(sttRecorder) return;
  await startMic();
  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
  sttRecorder = new MediaRecorder(micStream, mime ? { mimeType: mime } : undefined);
  sttRecorder.ondataavailable = async (ev) => {
    if(!ev.data || !ev.data.size || !sttEnabled) return;
    const ab = await ev.data.arrayBuffer();
    socket.emit('stt_chunk', { mime: ev.data.type || mime || 'audio/webm', b64: abToB64(ab), ts: Date.now() });
  };
  sttRecorder.start(500);
}

function stopSttMic(){
  try{ if(sttRecorder) sttRecorder.stop(); }catch(_){ }
  sttRecorder = null;
}

function stopCompositor(){
  if(drawRaf){ cancelAnimationFrame(drawRaf); drawRaf = null; }
  if(canvasStream){ canvasStream.getTracks().forEach(t=>t.stop()); canvasStream = null; }
}

function drawComposite(){
  if(!screenStream || !camStream) return;
  const ctx = compositeCanvas.getContext('2d');
  const w = compositeCanvas.width;
  const h = compositeCanvas.height;
  if(!ctx || !w || !h) return;

  ctx.clearRect(0,0,w,h);
  ctx.drawImage(screenSourceVideo, 0, 0, w, h);

  const r = pipRect;
  ctx.drawImage(camSourceVideo, r.x, r.y, r.w, r.h);
  ctx.lineWidth = 3;
  ctx.strokeStyle = '#00d4ff';
  ctx.strokeRect(r.x, r.y, r.w, r.h);
  ctx.fillStyle = '#00d4ff';
  ctx.fillRect(r.x + r.w - 12, r.y + r.h - 12, 12, 12);

  drawRaf = requestAnimationFrame(drawComposite);
}

function pointerPos(ev){
  const rect = compositeCanvas.getBoundingClientRect();
  const x = (ev.touches ? ev.touches[0].clientX : ev.clientX) - rect.left;
  const y = (ev.touches ? ev.touches[0].clientY : ev.clientY) - rect.top;
  return {x,y};
}

function bindPipInteractions(){
  function down(ev){
    const p = pointerPos(ev);
    const r = pipRect;
    const nearResize = (p.x > r.x + r.w - 20 && p.x < r.x + r.w + 6 && p.y > r.y + r.h - 20 && p.y < r.y + r.h + 6);
    const inside = (p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h);
    if(!inside && !nearResize) return;
    pipDrag = { mode: nearResize ? 'resize' : 'drag', ox: p.x - r.x, oy: p.y - r.y };
    ev.preventDefault();
  }
  function move(ev){
    if(!pipDrag) return;
    const p = pointerPos(ev);
    if(pipDrag.mode === 'drag'){
      pipRect.x = Math.max(0, Math.min(compositeCanvas.width - pipRect.w, p.x - pipDrag.ox));
      pipRect.y = Math.max(0, Math.min(compositeCanvas.height - pipRect.h, p.y - pipDrag.oy));
    } else {
      pipRect.w = Math.max(120, Math.min(compositeCanvas.width - pipRect.x, p.x - pipRect.x));
      pipRect.h = Math.max(80, Math.min(compositeCanvas.height - pipRect.y, p.y - pipRect.y));
    }
    ev.preventDefault();
  }
  function up(){ pipDrag = null; }

  compositeCanvas.addEventListener('mousedown', down);
  compositeCanvas.addEventListener('mousemove', move);
  window.addEventListener('mouseup', up);
  compositeCanvas.addEventListener('touchstart', down, {passive:false});
  compositeCanvas.addEventListener('touchmove', move, {passive:false});
  window.addEventListener('touchend', up, {passive:false});
}

async function startScreenShare(){
  try{
    const s = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    if(screenStream) screenStream.getTracks().forEach(t=>t.stop());
    screenStream = s;
    screenSourceVideo.srcObject = screenStream;
    await screenSourceVideo.play().catch(()=>{});

    if(!camStream) await startCam(currentVideoDeviceId);
    camSourceVideo.srcObject = camStream;
    await camSourceVideo.play().catch(()=>{});

    const st = screenStream.getVideoTracks()[0];
    const settings = st.getSettings ? st.getSettings() : {};
    compositeCanvas.width = settings.width || 1280;
    compositeCanvas.height = settings.height || 720;

    previewVideo.style.display='none';
    compositeCanvas.style.display='block';

    pipRect = {
      x: Math.round(compositeCanvas.width * 0.68),
      y: Math.round(compositeCanvas.height * 0.65),
      w: Math.round(compositeCanvas.width * 0.28),
      h: Math.round(compositeCanvas.height * 0.28),
    };

    stopCompositor();
    drawComposite();
    canvasStream = compositeCanvas.captureStream(30);

    st.onended = () => stopScreenShare();
    document.getElementById('shareScreen').textContent = 'Stop Share';
    await updateSendingTracks();
  }catch(e){
    console.warn('screen share failed', e);
  }
}

async function stopScreenShare(){
  if(screenStream){
    screenStream.getTracks().forEach(t=>t.stop());
    screenStream = null;
  }
  stopCompositor();
  compositeCanvas.style.display='none';
  previewVideo.style.display='block';
  previewVideo.srcObject = camStream;
  document.getElementById('shareScreen').textContent = 'Share Screen';
  await updateSendingTracks();
}

async function applyHighQualityAudioParameters(){
  if(!pc || !pc.getSenders) return;
  const audioSender = pc.getSenders().find(s => s.track && s.track.kind === 'audio');
  if(!audioSender) return;
  try {
    const params = audioSender.getParameters();
    if(!params.encodings || !params.encodings.length) params.encodings = [{}];
    params.encodings[0].maxBitrate = 128000;
    await audioSender.setParameters(params);
  } catch (e) {
    console.warn('audio sender setParameters failed', e);
  }
}

async function updateSendingTracks(){
  if(!pc) return;
  const senders = pc.getSenders ? pc.getSenders() : [];
  const audioSender = senders.find(s=>s.track&&s.track.kind==='audio');
  const videoSender = senders.find(s=>s.track&&s.track.kind==='video');

  const videoTrack = (screenStream && canvasStream && canvasStream.getVideoTracks()[0]) || (camStream && camStream.getVideoTracks()[0]) || null;
  const audioTrack = (micStream && micEnabled && micStream.getAudioTracks()[0]) || null;

  if(videoSender && videoSender.replaceTrack) await videoSender.replaceTrack(videoTrack);
  else if(videoTrack) pc.addTrack(videoTrack, (screenStream && canvasStream) || camStream);

  if(audioSender && audioSender.replaceTrack) await audioSender.replaceTrack(audioTrack);
  else if(audioTrack) pc.addTrack(audioTrack, micStream);

  await applyHighQualityAudioParameters();
}

async function initPc(){
  if(pc){ try{pc.close();}catch{} }
  pc=new RTCPeerConnection({iceServers});
  setConnBadge('connecting');
  pc.onconnectionstatechange = () => { pcStateEl.textContent = pc.connectionState; setConnBadge(pc.connectionState); };
  pc.oniceconnectionstatechange = () => { iceStateEl.textContent = pc.iceConnectionState; };
  pc.onicecandidate = (e) => { if(e.candidate) socket.emit('webrtc_ice', { candidate: e.candidate }); };

  pc.addTransceiver('video',{direction:'sendonly'});
  pc.addTransceiver('audio',{direction:'sendonly'});
  await updateSendingTracks();

  const offer=await pc.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: true, voiceActivityDetection: false });
  await pc.setLocalDescription(offer);
  socket.emit('webrtc_offer',{sdp:pc.localDescription.sdp,type:pc.localDescription.type});
}

socket.on('webrtc_answer', async a=>{ if(pc){ await pc.setRemoteDescription(a); } });
socket.on('webrtc_ice_server', async payload => {
  if(!pc) return;
  try{ if(payload && payload.candidate) await pc.addIceCandidate(payload); }catch(e){ console.warn(e); }
});
socket.on('chat_message', addMsg);
socket.on('ai_chat_reply', addMsg);
socket.on('ai_transcript', p=>addMsg({sender:'stt',text:p.text,ts:p.ts}));
socket.on('ai_tts_audio', p=>{ if(p&&p.url){ const audio=new Audio(p.url); audio.play().catch(()=>{});} });
socket.on('ai_status', s=>{ aiModeSel.value=s.mode||'active'; });

camLight.parentElement.onclick = async ()=>{
  const on = camLabel.textContent.includes('On');
  if(on && camStream){ camStream.getTracks().forEach(t=>t.stop()); camStream=null; previewVideo.srcObject=null; }
  if(!on){ await startCam(currentVideoDeviceId); }
  setStatusLight(camLight,camLabel,!on?{colorClass:'c-green',label:'Cam: On',blinkClass:'blink-normal',glow:true}:{colorClass:'c-red',label:'Cam: Off',blinkClass:'blink-fast',glow:false});
  await updateSendingTracks();
};
micLight.parentElement.onclick = async ()=>{
  micEnabled = !micEnabled;
  if(micStream){ micStream.getAudioTracks().forEach(t=>t.enabled = micEnabled); }
  setStatusLight(micLight,micLabel,micEnabled?{colorClass:'c-green',label:'Mic: On',blinkClass:'blink-normal',glow:true}:{colorClass:'c-red',label:'Mic: Off',blinkClass:'blink-fast',glow:false});
  await updateSendingTracks();
};
aiLight.parentElement.onclick = ()=>{ aiOn=!aiOn; socket.emit(aiOn?'ai_enable':'ai_disable',{}); setStatusLight(aiLight,aiLabel,aiOn?{colorClass:'c-green',label:'AI: On',blinkClass:'blink-normal',glow:true}:{colorClass:'c-yellow',label:'AI: Off',blinkClass:'blink-slow',glow:false}); document.getElementById('aiToggle').textContent=aiOn?'AI on':'AI off'; };
aiModeSel.onchange = ()=> socket.emit('ai_mode_set',{mode:aiModeSel.value});

videoDevices.onchange = async ()=>{ currentVideoDeviceId=videoDevices.value; await startCam(currentVideoDeviceId); await updateSendingTracks(); };
resolutionSelect.onchange = async ()=>{ await startCam(currentVideoDeviceId); await updateSendingTracks(); };
document.getElementById('toggleCam').onclick = async ()=>{
  const mobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');
  if(mobile){
    facingMode = (facingMode === 'user') ? 'environment' : 'user';
    currentVideoDeviceId = null;
    await startCam(null);
  } else {
    await enumerateVideoDevices();
    const opts=Array.from(videoDevices.options).map(o=>o.value);
    if(opts.length<=1){ await startCam(currentVideoDeviceId); }
    else {
      const idx=opts.indexOf(currentVideoDeviceId);
      currentVideoDeviceId=opts[(idx+1)%opts.length];
      await startCam(currentVideoDeviceId);
    }
  }
  await updateSendingTracks();
};
document.getElementById('shareScreen').onclick = async ()=>{ if(screenStream) await stopScreenShare(); else await startScreenShare(); };
document.getElementById('micToggle').onclick = ()=> micLight.parentElement.click();
document.getElementById('noiseToggle').onclick = async ()=>{ noiseCancellationEnabled = !noiseCancellationEnabled; document.getElementById('noiseToggle').textContent = noiseCancellationEnabled ? 'NoiseCancel: on' : 'NoiseCancel: off'; stopMic(); await startMic(); stopSttMic(); await startSttMic(); await updateSendingTracks(); };
document.getElementById('aiToggle').onclick = ()=> aiLight.parentElement.click();

document.getElementById('wakeToggle').onclick = async ()=>{
  wakeLockEnabled = !wakeLockEnabled;
  updateWakeButton();
  await applyWakeLockState();
};

document.getElementById('recordToggle').onclick = ()=>{
  const btn=document.getElementById('recordToggle');
  if(!mediaRecorder){
    const source=(screenStream && canvasStream) || camStream;
    if(!source) return alert('No media to record');
    const stream = new MediaStream();
    const vt = (screenStream && canvasStream && canvasStream.getVideoTracks()[0]) || (camStream && camStream.getVideoTracks()[0]);
    const at = (micStream && micStream.getAudioTracks()[0]) || null;
    if(vt) stream.addTrack(vt);
    if(at) stream.addTrack(at);
    recordedChunks=[];
    try{mediaRecorder=new MediaRecorder(stream);}catch(e){return alert('Recording not supported: '+e);} 
    mediaRecorder.ondataavailable=ev=>{if(ev.data&&ev.data.size) recordedChunks.push(ev.data)};
    mediaRecorder.onstop=()=>{ const blob=new Blob(recordedChunks,{type:'video/webm'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='record_'+Date.now()+'.webm'; a.click(); mediaRecorder=null; btn.textContent='Record'; };
    mediaRecorder.start(); btn.textContent='Stop';
  } else mediaRecorder.stop();
};

document.getElementById('attachBtn').onclick = ()=> document.getElementById('file').click();
document.getElementById('file').onchange = async e=>{ const f=e.target.files[0]; if(!f) return; const array=await f.arrayBuffer(); const bytes=new Uint8Array(array); let binary=''; const chunkSize=0x8000; for(let i=0;i<bytes.length;i+=chunkSize) binary += String.fromCharCode.apply(null, bytes.subarray(i,i+chunkSize)); socket.emit('upload_file',{name:f.name,mime:f.type,content_base64:btoa(binary),sender:'broadcaster',text:'uploaded '+f.name}); };

function send(){ const text=chatInput.value.trim(); if(!text) return; socket.emit('chat_message',{sender:'broadcaster',text}); chatInput.value=''; }
document.getElementById('sendBtn').onclick = send;
chatInput.addEventListener('keydown', e=>{ if(e.key==='Enter'&&!e.ctrlKey){ e.preventDefault(); send(); }});
document.getElementById('webBtn').onclick = async ()=>{ const q=chatInput.value.trim(); if(!q) return; const r=await fetch('/ai/websearch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})}); const j=await r.json(); addMsg({sender:'web',text:JSON.stringify(j.results)}); };
document.getElementById('readBtn').onclick = ()=>{
  ttsEnabled = !ttsEnabled;
  document.getElementById('readBtn').textContent = ttsEnabled ? 'Read Aloud: on' : 'Read Aloud: off';
};

(async ()=>{
  await loadIceServers();
  await enumerateVideoDevices();
  await startCam(currentVideoDeviceId);
  await startMic();
  await startSttMic();
  await initPc();
  isBroadcasting = true;
  await initWakeLock();
  socket.emit('ai_mode_set',{mode:'active'});
})();

bindPipInteractions();
window.addEventListener('beforeunload', ()=>{ isBroadcasting = false; stopSttMic(); stopMic(); stopCompositor(); releaseWakeLock(); if(screenStream) screenStream.getTracks().forEach(t=>t.stop()); if(camStream) camStream.getTracks().forEach(t=>t.stop()); });
</script>
</body>
</html>
EOF_STATIC_BROADCAST_HTML




write_file "static/watch.html" <<'EOF_STATIC_WATCH_HTML'
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Watch</title>
<link rel="stylesheet" href="/static/css_app.css">
<script src="/static/socket.io.js"></script>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="brand"><h1>Watch</h1><div class="small-muted">Viewer</div></div>
    <div class="status-row">
      <div class="status-pill">
        <div id="ttsLight" class="status-light c-blue blink-slow"></div>
        <div class="status-label" id="ttsLabel">TTS: Off</div>
      </div>
      <div class="status-pill">
        <div id="aiModeLight" class="status-light c-green blink-normal status-glow"></div>
        <div class="status-label" id="aiModeLabel">AI Mode: active</div>
      </div>
    </div>
  </div>

  <div class="main-grid">
    <div class="video-card">
      <div class="video-frame">
        <video id="watchVideo" autoplay playsinline controls></video>
      </div>

      <div class="controls">
        <button id="ttsToggle" class="btn">TTS toggle</button>
        <button id="readBtn" class="btn">Read last</button>
      </div>
    </div>

    <div class="chat-card">
      <div class="chat-list" id="chat"></div>
      <div class="controls">
        <button id="attachBtn" class="btn">Attach</button>
        <button id="webBtn" class="btn">WebSearch</button>
        <input id="file" type="file" hidden>
      </div>
      <div class="chat-input">
        <textarea id="chatInput" class="chat-text" placeholder="Message — Enter to send, Ctrl+Enter newline"></textarea>
        <button id="sendBtn" class="icon-btn">➡</button>
      </div>
    </div>
  </div>
</div>

<script>
const socket = io(window.location.origin, { path: "/socket.io", transports: ["websocket"], upgrade: false });
const video = document.getElementById('watchVideo');
const chatList = document.getElementById('chat');
const chatInput = document.getElementById('chatInput');
const ttsLight = document.getElementById('ttsLight');
const ttsLabel = document.getElementById('ttsLabel');
const aiModeLight = document.getElementById('aiModeLight');
const aiModeLabel = document.getElementById('aiModeLabel');

let pc = null;
let tts = false;
let reconnectAttempts = 0;
let reconnectTimer = null;
let iceServers=[{urls:"stun:stun.l.google.com:19302"}];
let ttsEnabled = false;
let ttsSeenIds = new Set();
let lastTtsRequestAt = 0;

function ttsMsgId(m){
  const base = `${m.message_id||''}|${m.sender||''}|${m.ts||''}|${m.text||''}`;
  let h = 0;
  for(let i=0;i<base.length;i++){ h = ((h<<5)-h) + base.charCodeAt(i); h |= 0; }
  return m.message_id || `h${Math.abs(h)}`;
}

function shouldRequestTts(m){
  if(!ttsEnabled) return false;
  const sender = (m.sender||'').toLowerCase();
  if(sender === 'system') return false;
  const text = String(m.text||'').trim();
  if(!text) return false;
  if(text.startsWith('[STT unavailable]')) return false;
  return true;
}

function maybeRequestTts(m){
  if(!shouldRequestTts(m)) return;
  const id = ttsMsgId(m);
  if(ttsSeenIds.has(id)) return;
  const now = Date.now();
  if(now - lastTtsRequestAt < 800) return;
  ttsSeenIds.add(id);
  lastTtsRequestAt = now;
  socket.emit('ai_tts_request', { text: String(m.text||'').trim() });
}

function addMsg(m){
  const el = document.createElement('div');
  el.className = 'chat-entry';
  const time = new Date((m.ts||Date.now())).toLocaleTimeString();
  el.innerHTML = `<b>${m.sender||'user'}</b> <small>${time}</small><div>${escapeHtml(m.text||'')}</div>`;
  if(m.media_url){
    if(m.media_url.match(/\.(png|jpg|jpeg|gif)$/i)) el.innerHTML += `<img class="msg-media" src="${m.media_url}">`;
    else if((m.media_type||'').startsWith('audio')) el.innerHTML += `<audio controls src="${m.media_url}"></audio>`;
    else if((m.media_type||'').startsWith('video')) el.innerHTML += `<video class="msg-media" controls src="${m.media_url}"></video>`;
    else el.innerHTML += `<a href="${m.media_url}" target="_blank">download</a>`;
  }
  chatList.appendChild(el); chatList.scrollTop = chatList.scrollHeight;
  maybeRequestTts(m);
}

function escapeHtml(s){ return (s||'').replace(/[&<"'>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }

function setMode(mode){
  aiModeLight.classList.remove('c-green','c-yellow','blink-normal','blink-slow','status-glow');
  if(mode === 'active'){
    aiModeLight.classList.add('c-green','blink-normal','status-glow');
  }else{
    aiModeLight.classList.add('c-yellow','blink-slow');
  }
  aiModeLabel.textContent = `AI Mode: ${mode}`;
}



function sanitizeIceServers(raw){
  if(!Array.isArray(raw)) return [{urls:'stun:stun.l.google.com:19302'}];
  const cleaned = raw.map(entry => {
    if(!entry || !entry.urls) return null;
    const urls = Array.isArray(entry.urls) ? entry.urls : [entry.urls];
    const safe = urls.map(u => {
      if(typeof u !== 'string') return null;
      const s = u.trim();
      if(s.startsWith('stun:') || s.startsWith('turn:') || s.startsWith('turns:')) return s;
      return `turn:${s}:3478?transport=udp`;
    }).filter(Boolean);
    if(!safe.length) return null;
    return {
      urls: safe.length === 1 ? safe[0] : safe,
      username: entry.username,
      credential: entry.credential,
    };
  }).filter(Boolean);
  return cleaned.length ? cleaned : [{urls:'stun:stun.l.google.com:19302'}];
}

async function loadIceServers(){
  try{
    const r = await fetch('/webrtc/ice-config');
    if(!r.ok) return;
    const j = await r.json();
    if(j && Array.isArray(j.iceServers) && j.iceServers.length){
      iceServers = sanitizeIceServers(j.iceServers);
    }
  }catch(e){ console.warn('ice config fallback to stun only', e); }
}



function cleanupPeer(){
  if(pc){
    try { pc.ontrack = null; pc.onicecandidate = null; pc.close(); } catch(_){}
  }
  pc = null;
}

function scheduleReconnect(){
  if(reconnectTimer) return;
  const delay = Math.min(8000, 1000 * Math.max(1, reconnectAttempts + 1));
  reconnectAttempts += 1;
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    cleanupPeer();
    await start();
  }, delay);
}

async function start(){
  console.log('watch: start()');
  cleanupPeer();
  pc = new RTCPeerConnection({ iceServers });

  pc.onicecandidate = e => {
    console.debug('pc.onicecandidate', e && e.candidate);
    if(e.candidate){
      socket.emit('watch_ice', { candidate: e.candidate });
    }
  };
  pc.oniceconnectionstatechange = () => console.debug('pc.iceConnectionState', pc.iceConnectionState);
  pc.onconnectionstatechange = () => {
    console.debug('pc.connectionState', pc.connectionState);
    if(pc.connectionState === 'failed' || pc.connectionState === 'disconnected'){
      scheduleReconnect();
    }
  };
  pc.onicegatheringstatechange = () => console.debug('pc.iceGatheringState', pc.iceGatheringState);

  pc.addTransceiver('video',{direction:'recvonly'});
  pc.addTransceiver('audio',{direction:'recvonly'});

  // Robust ontrack handler: prefer e.streams[0], fall back to creating a MediaStream and adding tracks
  pc.ontrack = e => {
    console.log('ontrack event', e);
    try {
      if (e.streams && e.streams.length) {
        console.log('attaching e.streams[0] to video.srcObject');
        video.srcObject = e.streams[0];
        video.play().catch(() => {});
        return;
      }

      // fallback for aiortc or servers that don't set stream id
      if (!video._remoteStream) {
        video._remoteStream = new MediaStream();
        video.srcObject = video._remoteStream;
        video.play().catch(() => {});
        console.log('created fallback remote MediaStream');
      }

      // add the individual incoming track
      video._remoteStream.addTrack(e.track);
      console.log('added track to fallback MediaStream:', e.track.kind);
    } catch (err) {
      console.warn('ontrack handler error', err);
    }
  };

  const offer = await pc.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: true, voiceActivityDetection: false });
  await pc.setLocalDescription(offer);

  console.log('sending watcher offer with trickle ICE enabled');
  socket.emit('watch_start',{ sdp:pc.localDescription.sdp, type:pc.localDescription.type });
}

socket.on('watch_answer', async a => {
  try {
    console.log('received watch_answer', a);
    if(!pc){
      console.warn('no pc to setRemoteDescription');
      return;
    }
    await pc.setRemoteDescription({ type: a.type, sdp: a.sdp });
    reconnectAttempts = 0;
    console.log('remote description set');
  } catch (err) {
    console.error('setRemoteDescription error', err);
  }
});

socket.on('watch_ice_server', async payload => {
  if(!pc) return;
  try {
    if(payload && payload.candidate){
      await pc.addIceCandidate(payload);
    }
  } catch (e){
    console.warn('failed to add watch remote ICE candidate', e);
  }
});
socket.on('chat_message', addMsg);
socket.on('ai_chat_reply', addMsg);
socket.on('ai_transcript', p => addMsg({ sender:'stt', text:p.text, ts:p.ts }));
socket.on('ai_tts_audio', p => { if(p && p.url){ const a = new Audio(p.url); a.play().catch(()=>{}); }});
socket.on('ai_status', s => { setMode((s&&s.mode)||'active'); });
socket.on('watch_error', e => {
  console.error('watch_error', e);
  addMsg({ sender: 'system', text: (e && e.error) ? e.error : 'watch connection error', ts: Date.now() });
  scheduleReconnect();
});
socket.on('stream_started', () => {
  if(!pc || pc.connectionState === 'closed' || pc.connectionState === 'failed' || pc.connectionState === 'disconnected'){
    scheduleReconnect();
  }
});


document.getElementById('ttsToggle').onclick = () => {
  ttsEnabled = !ttsEnabled;
  document.getElementById('ttsToggle').textContent = ttsEnabled ? 'TTS: on' : 'TTS: off';
};

document.getElementById('readBtn').onclick = () => {
  const last = chatList.lastElementChild;
  if(last){
    const text = (last.textContent || last.innerText || '').trim();
    if(text && !text.startsWith('[STT unavailable]')){
      socket.emit('ai_tts_request',{ text });
    }
  }
};

function send(){
  const t = chatInput.value.trim();
  if(!t) return;
  socket.emit('chat_message',{ sender:'viewer', text:t });
  chatInput.value='';
}

document.getElementById('sendBtn').onclick = send;
chatInput.addEventListener('keydown', e => {
  if(e.key==='Enter' && !e.ctrlKey){
    e.preventDefault();
    send();
  }
});

document.getElementById('webBtn').onclick = async ()=>{
  const q = chatInput.value.trim();
  if(!q) return;
  const r = await fetch('/ai/websearch',{ method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ query: q }) });
  const j = await r.json();
  addMsg({ sender:'web', text: JSON.stringify(j.results) });
};

document.getElementById('attachBtn').onclick = ()=> document.getElementById('file').click();
document.getElementById('file').onchange = async e => {
  const f = e.target.files[0];
  if(!f) return;
  const a = await f.arrayBuffer();
  const bytes = new Uint8Array(a);
  let binary='';
  const chunkSize=0x8000;
  for(let i=0;i<bytes.length;i+=chunkSize) binary += String.fromCharCode.apply(null, bytes.subarray(i,i+chunkSize));
  socket.emit('upload_file',{ name:f.name, mime:f.type, content_base64:btoa(binary), sender:'viewer', text:'uploaded '+f.name });
};

(async ()=>{ await loadIceServers(); await start(); })();
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
for role in roles/aiplatform.user roles/speech.client roles/texttospeech.user; do
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
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
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

patch_socketio_clients
patch_socketio_server

attempt "chmod home execute" sudo chmod o+x "${APP_HOME}"
attempt "chmod app dir execute" sudo chmod o+x "${APP_DIR}"
attempt "chmod static readable" sudo chmod -R o+r "${APP_DIR}/static"

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

if [ "$SYSTEMD_ENABLED" = true ]; then
  SYSTEMD_PATH="/etc/systemd/system/${SYSTEMD_UNIT_NAME}"
  attempt "write systemd unit" sudo tee "${SYSTEMD_PATH}" >/dev/null <<SYSTEMD
[Unit]
Description=Broadcast ASGI app (Hypercorn)
After=network.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/bin/bash -lc 'if [ -x "${APP_DIR}/venv/bin/hypercorn" ]; then exec ${APP_DIR}/venv/bin/hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1; else exec hypercorn main:asgi_app --bind 127.0.0.1:8000 --workers 1; fi'
Restart=on-failure
RestartSec=10
LimitNOFILE=10000
LimitNPROC=5000
MemoryLimit=2G
Type=simple

[Install]
WantedBy=multi-user.target
SYSTEMD
  attempt "systemd daemon-reload" sudo systemctl daemon-reload
  attempt "restart coturn after config updates" sudo systemctl restart coturn
  attempt "restart nginx after config updates" sudo systemctl restart nginx
  attempt "systemd enable/start service" sudo systemctl enable --now "${SYSTEMD_UNIT_NAME}"
fi

attempt "npm install socket.io-client" sudo npm install --prefix "${APP_DIR}" socket.io-client
attempt "copy socket.io.js" sudo cp "${APP_DIR}/node_modules/socket.io-client/dist/socket.io.js" "${APP_DIR}/static/socket.io.js"
attempt "chmod socket.io.js" sudo chmod 644 "${APP_DIR}/static/socket.io.js"

rm -f "${ZIPNAME}" || true
( cd "${APP_DIR}" && zip -r "${ZIPNAME}" . >/dev/null 2>&1 ) || true
attempt "chown app dir" sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
attempt "restart broadcast after socket.io patches" bash -lc "sudo systemctl restart broadcast || sudo systemctl restart '${SYSTEMD_UNIT_NAME}'"

cat <<DONE
========================================
DONE. App created at: ${APP_DIR}
Zip: ${ZIPNAME}
Next steps:
  1) cd ${APP_DIR}
  2) ./install_and_setup_venv.sh
  3) source venv/bin/activate
  4) sudo systemctl restart ${SYSTEMD_UNIT_NAME}
  5) open https://${DOMAIN}/broadcast

Verification checklist:
  - sudo journalctl -u broadcast -n 80 --no-pager
  - If you still see Engine.IO POST handler errors, check nginx websocket Upgrade headers.
========================================
DONE
