#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${DOMAIN:-lftr.biz}"
INSTALL_USER="${INSTALL_USER:-jayson_tolleson}"
APP_DIR="${APP_DIR:-/home/${INSTALL_USER}/broadcast}"
VENV_DIR="${APP_DIR}/venv"
GOOGLE_PROJECT_ID="${GOOGLE_PROJECT_ID:-}"
GCP_KEY="${GCP_KEY:-}"
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"
VERTEX_MODEL="${VERTEX_MODEL:-gemini-2.5-flash}"
AI_PROVIDER="${AI_PROVIDER:-vertex}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@${DOMAIN}}"
GOOGLE_CLOUD_REGION="global"

log() { printf '[%s] %s\n' "$1" "$2"; }
fail() { printf '[ERROR] %s\n' "$1" >&2; exit 1; }

validate_static_layout() {
  [[ -d "$APP_DIR" ]] || fail "APP_DIR missing or not a directory: $APP_DIR"

  if [[ -e "$APP_DIR/static" && ! -d "$APP_DIR/static" ]]; then
    echo "[ERROR] static path exists but is not a directory: $APP_DIR/static"
    rm -f "$APP_DIR/static" || true
    fail "invalid static deployment path removed; rerun installer"
  fi

  [[ -d "$APP_DIR/static" ]] || fail "static directory missing: $APP_DIR/static"

  local required_files=(
    "$APP_DIR/static/index.html"
    "$APP_DIR/static/indexgfs.html"
    "$APP_DIR/static/broadcast.html"
    "$APP_DIR/static/watch.html"
  )
  for f in "${required_files[@]}"; do
    [[ -f "$f" ]] || {
      echo "[ERROR] required static file missing: $f"
      ls -la "$APP_DIR/static" || true
      fail "static deployment validation failed"
    }
  done
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
  validate_static_layout

  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip
  pip install -r "$APP_DIR/requirements.txt"
  pip install netCDF4 pydap
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

  local metadata_project=""
  local metadata_sa_email=""
  local gcp_detected="no"
  local attached_sa="no"
  local auth_mode="disabled"
  local vertex_enabled="no"
  local apis_attempted="no"

  if curl -fsS --max-time 2 -H 'Metadata-Flavor: Google'     http://metadata.google.internal/computeMetadata/v1/instance/id >/dev/null 2>&1; then
    gcp_detected="yes"
  fi

  if [[ -z "${GOOGLE_PROJECT_ID:-}" ]] && [[ -n "${GOOGLE_CLOUD_PROJECT:-}" ]]; then
    GOOGLE_PROJECT_ID="${GOOGLE_CLOUD_PROJECT}"
  fi

  if [[ -z "$GOOGLE_PROJECT_ID" ]] && command -v gcloud >/dev/null 2>&1; then
    GOOGLE_PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    GOOGLE_PROJECT_ID="${GOOGLE_PROJECT_ID//\(unset\)/}"
    GOOGLE_PROJECT_ID="$(echo "$GOOGLE_PROJECT_ID" | xargs || true)"
  fi

  if [[ -z "$GOOGLE_PROJECT_ID" ]] && [[ "$gcp_detected" == "yes" ]]; then
    metadata_project="$(curl -fsS --max-time 2 -H 'Metadata-Flavor: Google'       http://metadata.google.internal/computeMetadata/v1/project/project-id 2>/dev/null || true)"
    if [[ -n "$metadata_project" ]]; then
      GOOGLE_PROJECT_ID="$metadata_project"
    fi
  fi

  if [[ "$gcp_detected" == "yes" ]]; then
    metadata_sa_email="$(curl -fsS --max-time 2 -H 'Metadata-Flavor: Google'       http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email 2>/dev/null || true)"
    if [[ -n "$metadata_sa_email" ]]; then
      attached_sa="yes"
    fi
  fi

  local explicit_key=""
  if [[ -n "${GCP_KEY:-}" ]] && [[ -f "$GCP_KEY" ]]; then
    explicit_key="$GCP_KEY"
  elif [[ -f "/etc/broadcast/gcp-key.json" ]]; then
    explicit_key="/etc/broadcast/gcp-key.json"
    GCP_KEY="/etc/broadcast/gcp-key.json"
  fi

  if [[ "$attached_sa" == "yes" ]] && [[ -n "$GOOGLE_PROJECT_ID" ]]; then
    auth_mode="adc"
    vertex_enabled="yes"
    unset GOOGLE_APPLICATION_CREDENTIALS || true
  elif [[ -n "$explicit_key" ]]; then
    auth_mode="json_key"
    vertex_enabled="yes"
    export GOOGLE_APPLICATION_CREDENTIALS="$explicit_key"
    chmod 600 "$explicit_key" || true
  else
    auth_mode="disabled"
    vertex_enabled="no"
    echo "[WARN] No attached service account ADC and no valid JSON key; AI features disabled"
  fi

  export GOOGLE_CLOUD_REGION="global"
  export GOOGLE_CLOUD_PROJECT="${GOOGLE_PROJECT_ID}"

  mkdir -p /etc/broadcast
  local gcp_key_env=""
  if [[ -n "$explicit_key" ]]; then
    gcp_key_env="$explicit_key"
  fi
  cat > /etc/broadcast/install.env <<EOF
DOMAIN=${DOMAIN}
GOOGLE_PROJECT_ID=${GOOGLE_PROJECT_ID}
GOOGLE_CLOUD_PROJECT=${GOOGLE_PROJECT_ID}
MAPS_API_KEY=${MAPS_API_KEY:-}
GOOGLE_MAPS_API_KEY=${MAPS_API_KEY:-}
GOOGLE_CLOUD_REGION=global
GCP_KEY=${gcp_key_env}
VERTEX_LOCATION=${VERTEX_LOCATION}
VERTEX_MODEL=${VERTEX_MODEL}
AI_PROVIDER=${AI_PROVIDER}
AI_AUTH_MODE=${auth_mode}
VERTEX_ENABLED=${vertex_enabled}
GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS:-}
EOF

  if command -v gcloud >/dev/null 2>&1 && [[ -n "$GOOGLE_PROJECT_ID" ]]; then
    apis_attempted="yes"
    if gcloud config set project "$GOOGLE_PROJECT_ID" >/dev/null 2>&1; then
      gcloud services enable         aiplatform.googleapis.com         speech.googleapis.com         texttospeech.googleapis.com >/dev/null 2>&1 || true
    fi
  fi

  echo "[INFO] GCP detected: ${gcp_detected}"
  echo "[INFO] Project ID: ${GOOGLE_PROJECT_ID:-<missing>}"
  echo "[INFO] Attached service account: ${attached_sa}${metadata_sa_email:+ (${metadata_sa_email})}"
  echo "[INFO] AI auth mode: ${auth_mode}"
  echo "[INFO] APIs enable attempted: ${apis_attempted}"
  echo "[INFO] Vertex enabled: ${vertex_enabled}"
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
  validate_static_layout
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
