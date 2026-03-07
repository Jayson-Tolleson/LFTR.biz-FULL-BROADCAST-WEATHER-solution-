#!/usr/bin/env bash
set -euo pipefail

install_nginx_config() {
  [[ "$ENABLE_NGINX" == "true" ]] || { log_info "NGINX disabled; skipping"; return 0; }

  local tpl="${REPO_ROOT}/deploy/templates/nginx.conf.template"
  local tmp
  tmp="$(mktemp)"

  local cert_chain="${CERT_DIR}/fullchain.pem"
  local cert_key="${CERT_DIR}/privkey.pem"
  local listen_directives="listen 80;"
  local ssl_block=""
  local redirect_block=""
  local gfs_shared_block=""
  local gfs_locations=""

  if [[ "$ENABLE_TLS" == "true" ]]; then
    if [[ -f "$cert_chain" && -f "$cert_key" ]]; then
      listen_directives="listen 443 ssl;"
      ssl_block="ssl_certificate ${cert_chain};\n    ssl_certificate_key ${cert_key};"
      redirect_block="server {\n    listen 80;\n    server_name ${DOMAIN};\n    return 301 https://\\$host\\$request_uri;\n}"
    else
      log_warn "TLS enabled but certificates missing at ${CERT_DIR}; installing HTTP-only nginx config"
    fi
  fi

  if [[ "$ENABLE_GFS_PROXY" == "true" ]]; then
    gfs_shared_block="map \\\$http_upgrade \\\$connection_upgrade {\n    default upgrade;\n    ''      close;\n}\n\nupstream gfs_app {\n    server ${GFS_UPSTREAM_HOST}:${GFS_UPSTREAM_PORT};\n    keepalive 32;\n}"

    gfs_locations="location = /gfs {\n        return 301 /gfs/;\n    }\n\n    location /gfs/ {\n        proxy_pass http://gfs_app/;\n        proxy_http_version 1.1;\n\n        proxy_set_header Host \\\$host;\n        proxy_set_header X-Real-IP \\\$remote_addr;\n        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto \\\$scheme;\n        proxy_set_header X-Forwarded-Prefix /gfs;\n\n        proxy_read_timeout 300s;\n        proxy_send_timeout 300s;\n        proxy_connect_timeout 60s;\n        proxy_buffering off;\n    }\n\n    location /gfs/api/ {\n        proxy_pass http://gfs_app/api/;\n        proxy_http_version 1.1;\n\n        proxy_set_header Host \\\$host;\n        proxy_set_header X-Real-IP \\\$remote_addr;\n        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto \\\$scheme;\n        proxy_set_header X-Forwarded-Prefix /gfs;\n\n        proxy_read_timeout 300s;\n        proxy_send_timeout 300s;\n        proxy_connect_timeout 60s;\n    }\n\n    location = /gfs/health {\n        proxy_pass http://gfs_app/health;\n        proxy_http_version 1.1;\n\n        proxy_set_header Host \\\$host;\n        proxy_set_header X-Real-IP \\\$remote_addr;\n        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto \\\$scheme;\n    }\n\n    location /gfs/ws/ {\n        proxy_pass http://gfs_app/ws/;\n        proxy_http_version 1.1;\n\n        proxy_set_header Upgrade \\\$http_upgrade;\n        proxy_set_header Connection \\\$connection_upgrade;\n\n        proxy_set_header Host \\\$host;\n        proxy_set_header X-Real-IP \\\$remote_addr;\n        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto \\\$scheme;\n        proxy_set_header X-Forwarded-Prefix /gfs;\n\n        proxy_read_timeout 3600s;\n        proxy_send_timeout 3600s;\n        proxy_connect_timeout 60s;\n        proxy_buffering off;\n    }"
  fi

  render_template "$tpl" "$tmp" \
    DOMAIN "$DOMAIN" \
    APP_DIR "$APP_DIR" \
    APP_BIND_HOST "$APP_BIND_HOST" \
    APP_BIND_PORT "$APP_BIND_PORT" \
    LISTEN_DIRECTIVES "$listen_directives" \
    SSL_BLOCK "$ssl_block" \
    REDIRECT_BLOCK "$redirect_block" \
    GFS_SHARED_BLOCK "$gfs_shared_block" \
    GFS_LOCATIONS "$gfs_locations"

  run_required "install nginx site config" $SUDO cp "$tmp" "$NGINX_SITE_AVAILABLE"
  run_required "enable nginx site" $SUDO ln -sf "$NGINX_SITE_AVAILABLE" "$NGINX_SITE_ENABLED"
  run_required "nginx config test" $SUDO nginx -t
  run_required "restart nginx" $SUDO systemctl restart nginx

  rm -f "$tmp"
}
