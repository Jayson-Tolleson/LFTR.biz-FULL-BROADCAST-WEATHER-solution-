#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${DOMAIN:-lftr.biz}"
INSTALL_USER="${INSTALL_USER:-jayson_tolleson}"
APP_DIR="${APP_DIR:-/home/${INSTALL_USER}/broadcast}"
VENV_DIR="${APP_DIR}/venv"
GOOGLE_PROJECT_ID="${GOOGLE_PROJECT_ID:-}"
GCP_KEY="/etc/broadcast/gcp-key.json"
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
  chmod -R 755 "$APP_DIR"
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

  if [ -f "$GCP_KEY" ]; then
    export GOOGLE_APPLICATION_CREDENTIALS="$GCP_KEY"
    echo "[INFO] GCP credentials loaded"
  else
    echo "[WARN] GCP key missing — AI features disabled"
  fi

  export GOOGLE_CLOUD_REGION=global

  if command -v gcloud >/dev/null 2>&1; then
    if [ -n "$GOOGLE_PROJECT_ID" ] && [ -f "$GCP_KEY" ]; then

      echo "[INFO] Configuring Google Cloud project"

      gcloud config set project "$GOOGLE_PROJECT_ID"

      gcloud services enable \
        aiplatform.googleapis.com \
        speech.googleapis.com \
        maps-backend.googleapis.com

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

  cat > /etc/nginx/sites-available/broadcast <<'EOF'
# HTTP redirect
server {

    listen 80;
    server_name DOMAIN_PLACEHOLDER;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }

}

# HTTPS server
server {

    listen 443 ssl http2;
    server_name DOMAIN_PLACEHOLDER;

    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 200M;

    # static globe assets
    location /static/ {

        alias /home/jayson_tolleson/broadcast/static/;

        expires 7d;
        access_log off;

    }

    # uploaded videos
    location /uploads/ {

        alias /home/jayson_tolleson/broadcast/uploads/;

    }

    # SSE weather stream
    location /gfs/stream {

        proxy_pass http://127.0.0.1:8000;

        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_buffering off;
        proxy_cache off;

        proxy_read_timeout 3600;

    }

    # FastAPI / WebSocket routes
    location / {

        proxy_pass http://127.0.0.1:8000;

        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 3600;
        proxy_send_timeout 3600;

    }

}
EOF

  sed -i "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" /etc/nginx/sites-available/broadcast

  ln -sf /etc/nginx/sites-available/broadcast /etc/nginx/sites-enabled/broadcast
  rm -f /etc/nginx/sites-enabled/default

  if ! nginx -t; then
    echo "[ERROR] nginx configuration validation failed"
    exit 1
  fi

  systemctl restart nginx
  systemctl enable nginx
}
phase7_services() {
  echo "===== PHASE 7 — SYSTEMD SERVICES ====="
  cat > /etc/systemd/system/broadcast.service <<EOF
[Unit]
Description=Broadcast Weather Server
After=network.target

[Service]
User=jayson_tolleson
WorkingDirectory=/home/jayson_tolleson/broadcast
Environment=GOOGLE_CLOUD_REGION=${GOOGLE_CLOUD_REGION}
ExecStart=/home/jayson_tolleson/broadcast/venv/bin/hypercorn server.gfs_service:app --bind 127.0.0.1:8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

  cat > /etc/systemd/system/gfs.service <<EOF
[Unit]
Description=GFS Backend Service
After=network.target

[Service]
User=jayson_tolleson
WorkingDirectory=/home/jayson_tolleson/broadcast
Environment=GOOGLE_CLOUD_REGION=${GOOGLE_CLOUD_REGION}
ExecStart=${APP_DIR}/venv/bin/python gfs.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable broadcast
  systemctl restart broadcast
  systemctl enable gfs
  systemctl restart gfs
}

phase8_health() {
  echo "===== PHASE 8 — HEALTH CHECKS ====="
  for i in {1..20}; do
    if curl -s http://127.0.0.1:8000/health >/dev/null; then
      echo "[installer] backend ready"
      break
    fi
    sleep 2
    if [[ "$i" -eq 20 ]]; then
      fail "broadcast health endpoint check failed"
    fi
  done
  curl -fsS http://127.0.0.1:8000/gfs >/dev/null || fail "gfs endpoint check failed"
  curl -kfsS "https://$DOMAIN" >/dev/null || fail "public TLS endpoint check failed"
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
