#!/usr/bin/env bash
set -euo pipefail

configure_nginx_impl() {
  local conf="/etc/nginx/sites-available/broadcast.conf"
  systemctl start nginx
  sleep 2
  cat > "$conf" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /static/ {
        alias ${APP_DIR}/static/;
    }

    location /uploads/ {
        alias ${APP_DIR}/uploads/;
    }

    location / {
        proxy_pass http://${APP_BIND_HOST}:${APP_BIND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF
  rm -f /etc/nginx/sites-enabled/default || true
  ln -sf "$conf" /etc/nginx/sites-enabled/broadcast.conf
  nginx -t
  systemctl restart nginx
  systemctl enable nginx
}
