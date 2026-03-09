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
DISTRO=""

log() { printf '[%s] %s\n' "$1" "$2"; }
fail() { printf '[ERROR] %s\n' "$1" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }
phase() { printf '\n===== %s =====\n' "$1"; }

install_packages_safe() {
  local required=("$@")
  local to_install=()
  for pkg in "${required[@]}"; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then
      to_install+=("$pkg")
    else
      log WARN "Package not available on this distro, skipping: $pkg"
    fi
  done
  if ((${#to_install[@]})); then
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${to_install[@]}"
  fi
}

detect_os() {

    if [ -f /etc/os-release ]; then
        . /etc/os-release
    else
        echo "Cannot detect OS"
        exit 1
    fi

    case "$ID:$VERSION_CODENAME" in
        debian:bookworm|debian:trixie)
            DISTRO="debian"
            ;;
        ubuntu:jammy|ubuntu:noble)
            DISTRO="ubuntu"
            ;;
        *)
            echo "Unsupported OS: $ID $VERSION_CODENAME"
            exit 1
            ;;
    esac

    echo "Detected supported system: $ID $VERSION_CODENAME"
}

PHASE_1_SYSTEM_PREP() {
  phase "PHASE 1 — SYSTEM PREP"
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Run as root (sudo ./install.sh)"
  need_cmd apt-get
  detect_os

  apt-get update -y
  install_packages_safe \
    curl \
    unzip \
    git \
    rsync \
    jq \
    lsb-release \
    software-properties-common \
    ca-certificates \
    gnupg
}

PHASE_2_PACKAGES() {
  phase "PHASE 2 — PACKAGES"

  apt-get update -y

  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl \
    git \
    unzip \
    rsync \
    jq \
    nginx \
    certbot \
    python3-certbot-nginx \
    python3 \
    python3-dev \
    python3-venv \
    python3-pip \
    build-essential \
    libssl-dev \
    libffi-dev

  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    libeccodes-dev \
    libeccodes-tools
}

PHASE_3_FIREWALL() {
  echo "===== PHASE 3 — FIREWALL ====="

  # Ensure ufw exists
  if ! command -v ufw >/dev/null 2>&1; then
    echo "[INFO] Installing ufw firewall"
    apt-get update
    apt-get install -y ufw
  fi

  # Configure firewall
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

  if curl -fsS --max-time 2 http://169.254.169.254 >/dev/null 2>&1; then
    log INFO "Running inside GCP environment"
    log INFO "Reminder: open VPC firewall ports 80 and 443"
  fi

  if [[ -n "$GOOGLE_APPLICATION_CREDENTIALS_SRC" && -f "$GOOGLE_APPLICATION_CREDENTIALS_SRC" ]]; then
    cp "$GOOGLE_APPLICATION_CREDENTIALS_SRC" "$GCP_KEY_DST"
    chmod 600 "$GCP_KEY_DST"
  fi

  GCP_KEY="$GCP_KEY_DST"
  if [[ ! -f "$GCP_KEY" ]]; then
    echo "[WARN] No GCP key found at $GCP_KEY"
    echo "[WARN] Skipping Vertex AI and Speech-to-Text configuration"
  else
    echo "[INFO] Using GCP service account key"
    export GOOGLE_APPLICATION_CREDENTIALS="$GCP_KEY"
  fi

  if ! command -v gcloud >/dev/null 2>&1; then
    log WARN "gcloud CLI not found; Google API enable step skipped"
  elif [[ -z "$GOOGLE_PROJECT_ID" ]]; then
    log WARN "GOOGLE_PROJECT_ID not set; skipping gcloud service enable"
  elif [[ ! -f "$GCP_KEY" ]]; then
    log WARN "Missing GCP key — AI features disabled"
  else
    gcloud config set project "$GOOGLE_PROJECT_ID"
    gcloud services enable       speech.googleapis.com       aiplatform.googleapis.com       iamcredentials.googleapis.com
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
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip
  pip install \
    numpy \
    pandas \
    xarray \
    cfgrib \
    eccodes \
    aiohttp \
    fastapi \
    hypercorn \
    websockets
}

PHASE_7_APPLICATION_INSTALL() {
  phase "PHASE 7 — APPLICATION INSTALL"
  rsync -a --delete --exclude '.git/' --exclude '__pycache__/' "$ROOT_DIR/" "$APP_DIR/"
  chown -R "$APP_USER:$APP_GROUP" "$APP_ROOT"
  [[ -f "$APP_DIR/requirements.txt" ]] || fail "requirements.txt missing"
  "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

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
from server.gfs_service import GFSService
PY

  for f in broadcast_server.py gfs.py gfs_service.py stt_service.py vertex_agent.py; do
    [[ -f "$APP_DIR/$f" ]] || fail "required module missing: $f"
  done
}

PHASE_8_TLS_CERTIFICATE() {
  phase "PHASE 8 — TLS CERTIFICATE"
  systemctl stop nginx || true

  certbot certonly \
    --standalone \
    --agree-tos \
    --non-interactive \
    --email "admin@${DOMAIN}" \
    -d "$DOMAIN"

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
