#!/usr/bin/env bash
set -euo pipefail

install_nginx_config() {
  [[ "$ENABLE_NGINX" == "true" ]] || { log_info "NGINX disabled; skipping"; return 0; }

  local tpl="${REPO_ROOT}/deploy/templates/nginx.conf.template"
  local tmp
  tmp="$(mktemp)"

  local domain_www="${DOMAIN_WWW:-www.${DOMAIN}}"
  local cert_chain="${CERT_DIR}/fullchain.pem"
  local cert_key="${CERT_DIR}/privkey.pem"
  local map_block='map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '\''\''      close;\n}'

  local app_proxy_location="location / {\n        proxy_pass http://${APP_BIND_HOST}:${APP_BIND_PORT};\n        proxy_http_version 1.1;\n\n        proxy_set_header Upgrade \\\$http_upgrade;\n        proxy_set_header Connection \\\$connection_upgrade;\n        proxy_set_header Host \\\$host;\n        proxy_set_header X-Real-IP \\\$remote_addr;\n        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto \\\$scheme;\n\n        proxy_read_timeout 3600s;\n        proxy_send_timeout 3600s;\n    }"

  local static_locations="location = /favicon.ico {\n        alias ${APP_DIR}/static/favicon.ico;\n        access_log off;\n        log_not_found off;\n        expires 7d;\n    }\n\n    location /static/ {\n        alias ${APP_DIR}/static/;\n        access_log off;\n        expires -1;\n        add_header Cache-Control \"no-store, no-cache, must-revalidate, proxy-revalidate\";\n    }"

  local http_server_block=""
  local https_server_block=""

  if [[ "$ENABLE_TLS" == "true" && -f "$cert_chain" && -f "$cert_key" ]]; then
    http_server_block="server {\n    listen 80;\n    listen [::]:80;\n    server_name ${DOMAIN} ${domain_www};\n\n    return 301 https://\\$host\\$request_uri;\n}"

    https_server_block="server {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n    http2 on;\n\n    server_name ${DOMAIN} ${domain_www};\n\n    ssl_certificate ${cert_chain};\n    ssl_certificate_key ${cert_key};\n\n    client_max_body_size 100M;\n    keepalive_timeout 65;\n    server_tokens off;\n\n    ${static_locations}\n\n    ${app_proxy_location}\n}"
  else
    if [[ "$ENABLE_TLS" == "true" ]]; then
      log_warn "TLS enabled but certificates missing at ${CERT_DIR}; installing HTTP-only nginx config"
    fi

    http_server_block="server {\n    listen 80;\n    listen [::]:80;\n    server_name ${DOMAIN} ${domain_www};\n\n    client_max_body_size 100M;\n    keepalive_timeout 65;\n    server_tokens off;\n\n    ${static_locations}\n\n    ${app_proxy_location}\n}"
  fi

  render_template "$tpl" "$tmp" \
    MAP_BLOCK "$map_block" \
    HTTP_SERVER_BLOCK "$http_server_block" \
    HTTPS_SERVER_BLOCK "$https_server_block"

  run_required "install nginx site config" $SUDO cp "$tmp" "$NGINX_SITE_AVAILABLE"
  run_required "enable nginx site" $SUDO ln -sf "$NGINX_SITE_AVAILABLE" "$NGINX_SITE_ENABLED"
  run_required "nginx config test" $SUDO nginx -t
  run_required "restart nginx" $SUDO systemctl restart nginx

  rm -f "$tmp"
}
