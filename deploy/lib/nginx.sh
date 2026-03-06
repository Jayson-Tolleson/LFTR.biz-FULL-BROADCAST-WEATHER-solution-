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

  if [[ "$ENABLE_TLS" == "true" ]]; then
    if [[ -f "$cert_chain" && -f "$cert_key" ]]; then
      listen_directives="listen 443 ssl;"
      ssl_block="ssl_certificate ${cert_chain};\n    ssl_certificate_key ${cert_key};"
      redirect_block="server {\n    listen 80;\n    server_name ${DOMAIN};\n    return 301 https://\\$host\\$request_uri;\n}"
    else
      log_warn "TLS enabled but certificates missing at ${CERT_DIR}; installing HTTP-only nginx config"
    fi
  fi

  render_template "$tpl" "$tmp" \
    DOMAIN "$DOMAIN" \
    APP_DIR "$APP_DIR" \
    APP_BIND_HOST "$APP_BIND_HOST" \
    APP_BIND_PORT "$APP_BIND_PORT" \
    LISTEN_DIRECTIVES "$listen_directives" \
    SSL_BLOCK "$ssl_block" \
    REDIRECT_BLOCK "$redirect_block"

  run_required "install nginx site config" $SUDO cp "$tmp" "$NGINX_SITE_AVAILABLE"
  run_required "enable nginx site" $SUDO ln -sf "$NGINX_SITE_AVAILABLE" "$NGINX_SITE_ENABLED"
  run_required "nginx config test" $SUDO nginx -t
  run_required "restart nginx" $SUDO systemctl restart nginx

  rm -f "$tmp"
}
