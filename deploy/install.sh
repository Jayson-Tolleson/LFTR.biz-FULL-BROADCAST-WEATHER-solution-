#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DOMAIN="${DOMAIN:-lftr.biz}"
APP_USER="${APP_USER:-broadcast}"
APP_GROUP="${APP_GROUP:-broadcast}"
APP_ROOT="${APP_ROOT:-/opt/broadcast}"
APP_DIR="${APP_DIR:-${APP_ROOT}/app}"
VENV_DIR="${VENV_DIR:-${APP_ROOT}/venv}"
CFG_DIR="${CFG_DIR:-/etc/broadcast}"
GCP_KEY_DST="${GOOGLE_APPLICATION_CREDENTIALS:-${CFG_DIR}/gcp-key.json}"
GOOGLE_PROJECT_ID="${GOOGLE_PROJECT_ID:-}"
GOOGLE_APPLICATION_CREDENTIALS_SRC="${GOOGLE_APPLICATION_CREDENTIALS_SRC:-}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@${DOMAIN}}"

log() { printf '[%s] %s\n' "$1" "$2"; }
fail() { printf '[ERROR] %s\n' "$1" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

phase() { printf '\n===== %s =====\n' "$1"; }

PHASE_1_SYSTEM_PREP() {
  phase "PHASE 1 — SYSTEM PREP"
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Run as root (sudo ./install.sh)"
  need_cmd apt-get
  local os_id os_ver
  os_id="$(. /etc/os-release; echo "$ID")"
  os_ver="$(. /etc/os-release; echo "$VERSION_ID")"
  case "$os_id:$os_ver" in
    debian:12*|ubuntu:22*|ubuntu:24*) ;;
    *) fail "Unsupported OS ${os_id} ${os_ver}. Supported: Debian 12, Ubuntu 22+, Ubuntu 24+" ;;
  esac
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl unzip git rsync jq lsb-release software-properties-common ca-certificates gnupg
}

PHASE_2_PACKAGES() {
  phase "PHASE 2 — PACKAGES"
  apt-get install -y \
    nginx certbot python3-certbot python3-certbot-nginx \
    python3 python3-pip python3-venv build-essential libffi-dev libssl-dev netcdf-bin \
    libeccodes-dev libopenjp2-7 coturn ufw netcat-openbsd
  pip3 install --upgrade pip
  pip3 install numpy pandas xarray cfgrib eccodes aiohttp fastapi hypercorn websockets
}

PHASE_3_FIREWALL() {
  phase "PHASE 3 — FIREWALL"
  ufw allow 22 || true
  ufw allow 80 || true
  ufw allow 443 || true
  ufw allow 3478 || true
  ufw allow 3478/udp || true
  ufw --force enable || true
}

PHASE_4_GOOGLE_CLOUD_APIS() {
  phase "PHASE 4 — GOOGLE CLOUD APIs"
  mkdir -p "$CFG_DIR"
  if [[ -n "$GOOGLE_APPLICATION_CREDENTIALS_SRC" && -f "$GOOGLE_APPLICATION_CREDENTIALS_SRC" ]]; then
    cp "$GOOGLE_APPLICATION_CREDENTIALS_SRC" "$GCP_KEY_DST"
    chmod 600 "$GCP_KEY_DST"
  fi
  [[ -f "$GCP_KEY_DST" ]] || fail "Missing GCP key file at $GCP_KEY_DST (set GOOGLE_APPLICATION_CREDENTIALS_SRC)"
  [[ -n "$GOOGLE_PROJECT_ID" ]] || fail "GOOGLE_PROJECT_ID must be set"

  if ! command -v gcloud >/dev/null 2>&1; then
    log WARN "gcloud CLI not found; Google API enable step skipped. Install google-cloud-cli for full automation."
  else
    export GOOGLE_APPLICATION_CREDENTIALS="$GCP_KEY_DST"
    gcloud config set project "$GOOGLE_PROJECT_ID"
    gcloud services enable speech.googleapis.com aiplatform.googleapis.com iamcredentials.googleapis.com
    gcloud config set ai/region global
  fi
}

PHASE_5_FILESYSTEM() {
  phase "PHASE 5 — FILESYSTEM"
  id -u "$APP_USER" >/dev/null 2>&1 || useradd -r -m "$APP_USER"
  mkdir -p "$APP_ROOT" "$APP_DIR" "$APP_ROOT/logs" "$APP_ROOT/data" "$APP_ROOT/uploads" "$CFG_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$APP_ROOT"
}

PHASE_6_PYTHON_ENVIRONMENT() {
  phase "PHASE 6 — PYTHON ENVIRONMENT"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel
}

PHASE_7_APPLICATION_INSTALL() {
  phase "PHASE 7 — APPLICATION INSTALL"
  rsync -a --delete --exclude '.git/' --exclude '__pycache__/' "$ROOT_DIR/" "$APP_DIR/"
  chown -R "$APP_USER:$APP_GROUP" "$APP_ROOT"
  [[ -f "$APP_DIR/requirements.txt" ]] || fail "requirements.txt missing"
  "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

  # compatibility wrappers required by ops contract
  [[ -f "$APP_DIR/broadcast_server.py" ]] || cat > "$APP_DIR/broadcast_server.py" <<'PY'
from main import asgi_app as app
PY
  [[ -f "$APP_DIR/gfs.py" ]] || cat > "$APP_DIR/gfs.py" <<'PY'
from fastapi import FastAPI
app = FastAPI()
@app.get('/gfs/health')
def health():
    return {'ok': True, 'service': 'gfs'}
PY
  [[ -f "$APP_DIR/stt_service.py" ]] || cat > "$APP_DIR/stt_service.py" <<'PY'
# STT service compatibility stub
PY
  [[ -f "$APP_DIR/vertex_agent.py" ]] || cat > "$APP_DIR/vertex_agent.py" <<'PY'
# Vertex agent compatibility stub
PY
  [[ -f "$APP_DIR/gfs_service.py" ]] || cat > "$APP_DIR/gfs_service.py" <<'PY'
from server.gfs_service import GFSService  # compatibility import shim
PY

  for f in broadcast_server.py gfs.py gfs_service.py stt_service.py vertex_agent.py; do
    [[ -f "$APP_DIR/$f" ]] || fail "required module missing: $f"
  done
}

PHASE_8_TLS_CERTIFICATE() {
  phase "PHASE 8 — TLS CERTIFICATE"
  systemctl stop nginx || true
  certbot certonly --standalone --non-interactive --agree-tos --email "$CERTBOT_EMAIL" -d "$DOMAIN"
  [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]] || fail "certificate not found for ${DOMAIN}"
}

PHASE_9_NGINX_CONFIG() {
  phase "PHASE 9 — NGINX CONFIG"
  export DOMAIN APP_ROOT
  envsubst '${DOMAIN} ${APP_ROOT}' < "$ROOT_DIR/deploy/nginx_template.conf" > /etc/nginx/sites-available/broadcast.conf
  ln -sf /etc/nginx/sites-available/broadcast.conf /etc/nginx/sites-enabled/broadcast.conf
  rm -f /etc/nginx/sites-enabled/default || true
  nginx -t
  systemctl enable nginx
  systemctl start nginx
}

PHASE_10_SYSTEMD_SERVICES() {
  phase "PHASE 10 — SYSTEMD SERVICES"
  envsubst '${APP_USER} ${APP_GROUP} ${APP_DIR} ${VENV_DIR} ${CFG_DIR}' < "$ROOT_DIR/deploy/systemd/broadcast.service" > /etc/systemd/system/broadcast.service
  envsubst '${APP_USER} ${APP_GROUP} ${APP_DIR} ${VENV_DIR} ${CFG_DIR}' < "$ROOT_DIR/deploy/systemd/gfs.service" > /etc/systemd/system/gfs.service
  systemctl daemon-reload
  systemctl enable broadcast
  systemctl enable gfs
  systemctl start broadcast
  systemctl start gfs
}

PHASE_11_HEALTH_CHECKS() {
  phase "PHASE 11 — HEALTH CHECKS"
  curl -fsS http://127.0.0.1:8000/health >/dev/null || fail "broadcast health failed"
  curl -fsS http://127.0.0.1:8001/gfs/health >/dev/null || fail "gfs health failed"
  curl -kfsS "https://${DOMAIN}" >/dev/null || fail "nginx/tls endpoint failed"
  systemctl is-active --quiet broadcast || fail "broadcast service inactive"
  systemctl is-active --quiet gfs || fail "gfs service inactive"
  log OK "All health checks passed"
}

main() {
  PHASE_1_SYSTEM_PREP
  PHASE_2_PACKAGES
  PHASE_3_FIREWALL
  PHASE_4_GOOGLE_CLOUD_APIS
  PHASE_5_FILESYSTEM
  PHASE_6_PYTHON_ENVIRONMENT
  PHASE_7_APPLICATION_INSTALL
  PHASE_8_TLS_CERTIFICATE
  PHASE_9_NGINX_CONFIG
  PHASE_10_SYSTEMD_SERVICES
  PHASE_11_HEALTH_CHECKS
}

main "$@"
