#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${DOMAIN:-lftr.biz}"
INSTALL_USER="${INSTALL_USER:-jayson_tolleson}"
APP_DIR="${APP_DIR:-/home/${INSTALL_USER}/broadcast}"
VENV_DIR="${APP_DIR}/venv"
GOOGLE_PROJECT_ID="${GOOGLE_PROJECT_ID:-}"
GCP_KEY="${GCP_KEY:-/etc/broadcast/gcp-key.json}"
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"
VERTEX_MODEL="${VERTEX_MODEL:-gemini-2.5-flash}"
AI_PROVIDER="${AI_PROVIDER:-vertex}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@${DOMAIN}}"
GOOGLE_CLOUD_REGION="global"

log() { printf '[%s] %s\n' "$1" "$2"; }
fail() { printf '[ERROR] %s\n' "$1" >&2; exit 1; }

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

phase1_system_prep() {
  echo "===== PHASE 1 — SYSTEM PREP ====="
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Run installer as root: sudo bash broadcast.sh"
  detect_os

  apt-get update
  apt-get install -y \
    curl \
    wget \
    git \
    rsync \
    unzip \
    gnupg \
    ca-certificates \
    lsb-release \
    python3 \
    python3-venv \
    python3-pip \
    build-essential \
    gfortran \
    libeccodes-dev \
    libeccodes-tools \
    libnetcdf-dev \
    libhdf5-dev
}

phase2_python_runtime() {
  echo "===== PHASE 2 — PYTHON RUNTIME ====="
  id -u "$INSTALL_USER" >/dev/null 2>&1 || useradd -m "$INSTALL_USER"
  mkdir -p "$APP_DIR" /etc/broadcast
  rsync -a --delete --exclude '.git/' --exclude '__pycache__/' "$ROOT_DIR/" "$APP_DIR/"
  chown -R "$INSTALL_USER:$INSTALL_USER" "$APP_DIR"

  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip
  pip install -r "$APP_DIR/requirements.txt"
  pip install cfgrib eccodes
  find "$APP_DIR" -type d -exec chmod 755 {} \;
  find "$APP_DIR" -type f -exec chmod 644 {} \;
  find "$APP_DIR" -type f \( -name "*.sh" -o -path "*/venv/bin/*" \) -exec chmod 755 {} \;
  chmod o+x "/home/${INSTALL_USER}"
}

phase3_firewall() {
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
  ufw --force enable || true
}

phase4_google_cloud() {
  echo "===== PHASE 4 — GOOGLE CLOUD ====="
  if curl -fsS --max-time 2 http://169.254.169.254 >/dev/null 2>&1; then
    echo "Running inside GCP environment"
    echo "[INFO] Ensure firewall ports 80 and 443 are open in VPC rules"
  fi

  if [ -f "/etc/broadcast/gcp-key.json" ]; then
    export GOOGLE_APPLICATION_CREDENTIALS="/etc/broadcast/gcp-key.json"
    GCP_KEY="/etc/broadcast/gcp-key.json"
    echo "[INFO] GCP credentials loaded"
  elif [ -f "$GCP_KEY" ]; then
    export GOOGLE_APPLICATION_CREDENTIALS="$GCP_KEY"
    echo "[INFO] GCP credentials loaded"
  else
    echo "[WARN] GCP key missing — AI features disabled"
  fi

  export GOOGLE_CLOUD_REGION=global

  mkdir -p /etc/broadcast
  cat > /etc/broadcast/install.env <<EOF
DOMAIN=${DOMAIN}
GOOGLE_PROJECT_ID=${GOOGLE_PROJECT_ID}
GOOGLE_CLOUD_PROJECT=${GOOGLE_PROJECT_ID}
MAPS_API_KEY=${MAPS_API_KEY:-}
GOOGLE_MAPS_API_KEY=${MAPS_API_KEY:-}
GOOGLE_CLOUD_REGION=global
GCP_KEY=${GCP_KEY}
VERTEX_LOCATION=${VERTEX_LOCATION}
VERTEX_MODEL=${VERTEX_MODEL}
AI_PROVIDER=${AI_PROVIDER}
EOF

  if command -v gcloud >/dev/null 2>&1; then
    if [ -n "$GOOGLE_PROJECT_ID" ] && [ -f "$GCP_KEY" ]; then

      echo "[INFO] Configuring Google Cloud project"

      gcloud config set project "$GOOGLE_PROJECT_ID"

      gcloud services enable \
        aiplatform.googleapis.com \
        speech.googleapis.com \
        texttospeech.googleapis.com

      echo "[INFO] Google APIs enabled"

    else
      echo "[WARN] Skipping API enablement (missing project or key)"
    fi
  else
    echo "[WARN] gcloud CLI not installed — skipping API setup"
  fi
}

phase5_tls() {
  echo "===== PHASE 5 — TLS CERTIFICATE ====="
  apt-get install -y certbot python3-certbot-nginx nginx
  if [[ "${SKIP_SSL:-0}" == "1" ]]; then
    echo "[INFO] SKIP_SSL=1, skipping certificate issuance"
    return 0
  fi
  if [[ -z "${DOMAIN:-}" ]]; then
    echo "[WARN] DOMAIN empty, skipping certificate issuance"
    return 0
  fi
  if [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "[WARN] DOMAIN looks like an IP, skipping certificate issuance"
    return 0
  fi
  systemctl stop nginx || true
  certbot certonly \
    --standalone \
    --agree-tos \
    --non-interactive \
    --email "$CERTBOT_EMAIL" \
    -d "$DOMAIN"

  [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] || fail "TLS certificate not found at /etc/letsencrypt/live/$DOMAIN/fullchain.pem"
}

phase6_nginx() {
  echo "===== PHASE 6 — NGINX SETUP ====="
  apt-get install -y nginx
  mkdir -p /var/www/certbot

  cp "$ROOT_DIR/deploy/nginx_template.conf" /etc/nginx/sites-available/broadcast
  sed -i "s/\${DOMAIN}/$DOMAIN/g" /etc/nginx/sites-available/broadcast
  sed -i "s|\${APP_USER}|$INSTALL_USER|g" /etc/nginx/sites-available/broadcast
  sed -i "s|\${APP_ROOT}|$APP_DIR|g" /etc/nginx/sites-available/broadcast

  rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/broadcast /etc/nginx/sites-enabled/broadcast.conf /etc/nginx/sites-enabled/broadcast_stack
  ln -sf /etc/nginx/sites-available/broadcast /etc/nginx/sites-enabled/broadcast

  if ! nginx -t; then
    echo "[ERROR] nginx configuration validation failed"
    exit 1
  fi

  systemctl restart nginx
  systemctl enable nginx
}
phase7_services() {
  echo "===== PHASE 7 — SYSTEMD SERVICES ====="
  cp "$ROOT_DIR/deploy/systemd/broadcast.service" /etc/systemd/system/broadcast.service
  sed -i "s|\${APP_USER}|$INSTALL_USER|g" /etc/systemd/system/broadcast.service
  sed -i "s|\${APP_GROUP}|$INSTALL_USER|g" /etc/systemd/system/broadcast.service
  sed -i "s|\${CFG_DIR}|/etc/broadcast|g" /etc/systemd/system/broadcast.service


  systemctl daemon-reload
  systemctl enable broadcast
  systemctl restart broadcast
}

phase8_health() {
  echo "===== PHASE 8 — HEALTH CHECKS ====="
  for i in {1..20}; do
    if curl -s http://127.0.0.1:8000/health >/dev/null; then
      echo "[installer] backend ready"
      break
    fi
    sleep 1
    if [[ "$i" -eq 20 ]]; then
      fail "broadcast health endpoint check failed"
    fi
  done
  curl -fsS http://127.0.0.1:8000/gfs/api/health >/dev/null || fail "gfs api health check failed"
  if [[ "${SKIP_SSL:-0}" != "1" ]]; then
    curl -kfsS "https://$DOMAIN" >/dev/null || fail "public TLS endpoint check failed"
  fi
  if [ ! -f "$APP_DIR/static/indexgfs.html" ]; then
    echo "[installer] static assets missing"
    exit 1
  fi
  echo "[OK] Installer completed successfully"
}

main() {
  phase1_system_prep
  phase2_python_runtime
  phase3_firewall
  phase4_google_cloud
  phase5_tls
  phase6_nginx
  phase7_services
  phase8_health
}

main "$@"
