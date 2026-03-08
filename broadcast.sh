#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$USER}"
APP_HOME="/home/${APP_USER}"
APP_DIR="${APP_HOME}/broadcast"
APP_PORT="8000"

if [[ ! -d "$APP_HOME" ]]; then
  APP_HOME="$HOME"
  APP_DIR="${APP_HOME}/broadcast"
fi

require_root() {
  if [[ "$EUID" -ne 0 ]]; then
    echo "Please run with sudo: sudo ./broadcast.sh"
    exit 1
  fi
}

detect_os() {
  if [[ ! -f /etc/os-release ]]; then
    echo "Unsupported OS: missing /etc/os-release"
    exit 1
  fi
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" ]]; then
    echo "Unsupported OS: ${ID:-unknown}. This installer supports Ubuntu/Debian."
    exit 1
  fi
  echo "Detected OS: ${PRETTY_NAME:-$ID}"
}

prompt_with_default() {
  local prompt="$1"
  local default="$2"
  local out
  read -r -p "$prompt [$default]: " out
  if [[ -z "$out" ]]; then
    echo "$default"
  else
    echo "$out"
  fi
}

install_system_packages() {
  echo "[1/12] Installing system packages"
  apt update
  DEBIAN_FRONTEND=noninteractive apt install -y \
    python3 python3-venv python3-pip nginx coturn certbot python3-certbot-nginx ufw git ffmpeg rsync openssl
}

install_app_runtime() {
  echo "[2/12] Installing application runtime to ${APP_DIR}"
  mkdir -p "$APP_DIR" "$APP_DIR/logs" "$APP_DIR/uploads" "$APP_DIR/uploads/video/locations" "$APP_DIR/data"

  rsync -av --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    "$REPO_ROOT/broadcast/" "$APP_DIR/"

  cd "$APP_DIR"

  python3 -m venv venv
  source venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  deactivate

  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
}

configure_env() {
  echo "[3/12] Configuring .env"
  local default_domain="$(hostname -f 2>/dev/null || hostname)"
  DOMAIN="$(prompt_with_default 'Enter domain' "$default_domain")"
  JWT_SECRET="$(prompt_with_default 'Enter JWT secret' "$(openssl rand -hex 24)")"
  TURN_USERNAME="$(prompt_with_default 'Enter TURN username' 'webrtc')"
  TURN_PASSWORD="$(prompt_with_default 'Enter TURN password' "$(openssl rand -hex 12)")"
  TURN_URL="$(prompt_with_default 'Enter TURN URL (turn:domain:3478)' "turn:${DOMAIN}:3478")"
  GOOGLE_MAPS_API_KEY="$(prompt_with_default 'Enter Google Maps API key' '')"
  VERTEX_PROJECT_ID="$(prompt_with_default 'Enter Vertex project id' 'broadcasterfishmap')"

  cat > "${APP_DIR}/.env" <<ENV
DOMAIN=${DOMAIN}
JWT_SECRET=${JWT_SECRET}
TURN_URL=${TURN_URL}
TURN_USERNAME=${TURN_USERNAME}
TURN_PASSWORD=${TURN_PASSWORD}
GOOGLE_MAPS_API_KEY=${GOOGLE_MAPS_API_KEY}
VERTEX_PROJECT_ID=${VERTEX_PROJECT_ID}
PORT=${APP_PORT}
ENV

  chown "$APP_USER:$APP_USER" "${APP_DIR}/.env"
}

configure_coturn() {
  echo "[4/12] Configuring coturn"
  local external_ip
  external_ip="$(curl -4 -s https://ifconfig.me || true)"

  cat > /etc/turnserver.conf <<TURN
listening-port=3478
tls-listening-port=5349
fingerprint
lt-cred-mech
realm=${DOMAIN}
server-name=${DOMAIN}
user=${TURN_USERNAME}:${TURN_PASSWORD}
no-cli
no-loopback-peers
no-multicast-peers
min-port=49160
max-port=49200
TURN

  if [[ -n "$external_ip" ]]; then
    echo "external-ip=${external_ip}" >> /etc/turnserver.conf
  fi

  sed -i 's/^#\?TURNSERVER_ENABLED=.*/TURNSERVER_ENABLED=1/' /etc/default/coturn || true
  systemctl enable coturn
  systemctl restart coturn
}

configure_firewall() {
  echo "[5/12] Configuring UFW"
  ufw allow 22/tcp || true
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
  ufw allow 3478/tcp || true
  ufw allow 3478/udp || true
  ufw allow 5349/tcp || true
  ufw allow 49160:49200/udp || true
  ufw --force enable || true
}

configure_nginx() {
  echo "[7/12] Configuring nginx"
  cat > /etc/nginx/sites-available/broadcast <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};

    location /static/ {
        alias ${APP_DIR}/static/;
        expires 1h;
    }

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600;
    }
}
NGINX

  ln -sf /etc/nginx/sites-available/broadcast /etc/nginx/sites-enabled/broadcast
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl restart nginx
}

configure_ssl() {
  echo "[6/12] Configuring SSL certificates"
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}" --redirect; then
    echo "Certbot certificate installed"
    return
  fi

  echo "Certbot failed, installing self-signed fallback certificate"
  mkdir -p /etc/ssl/private /etc/ssl/certs
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout /etc/ssl/private/broadcast-selfsigned.key \
    -out /etc/ssl/certs/broadcast-selfsigned.crt \
    -subj "/CN=${DOMAIN}"

  cat > /etc/nginx/sites-available/broadcast <<NGINXSSL
server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name ${DOMAIN};

    ssl_certificate /etc/ssl/certs/broadcast-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/broadcast-selfsigned.key;

    location /static/ {
        alias ${APP_DIR}/static/;
        expires 1h;
    }

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600;
    }
}
NGINXSSL

  nginx -t
  systemctl restart nginx
}

configure_systemd() {
  echo "[9/12] Configuring systemd service"
  cat > /etc/systemd/system/broadcast.service <<SYSTEMD
[Unit]
Description=Broadcast Weather Quart/Hypercorn
After=network.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/venv/bin/hypercorn server.app:app --bind 127.0.0.1:${APP_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMD

  systemctl daemon-reload
  systemctl enable broadcast
  systemctl restart broadcast
}

validate_services() {
  echo "[11/12] Validating services"
  systemctl --no-pager --full status nginx || true
  systemctl --no-pager --full status coturn || true
  systemctl --no-pager --full status broadcast || true
}

final_output() {
  echo "[12/12] Broadcast platform installed successfully"
  echo
  echo "Access your system:"
  echo "https://${DOMAIN}"
  echo
  echo "Verification pages:"
  echo "https://${DOMAIN}/static/indexgfs.html"
  echo "https://${DOMAIN}/static/broadcast.html"
}

main() {
  require_root
  detect_os
  install_system_packages
  install_app_runtime
  configure_env
  configure_coturn
  configure_firewall
  configure_nginx
  configure_ssl
  configure_systemd
  validate_services
  final_output
}

main "$@"
