#!/usr/bin/env bash
set -euo pipefail

install_coturn_config() {
  [[ "$ENABLE_COTURN" == "true" ]] || { log_info "coturn disabled; skipping"; return 0; }

  local tpl="${REPO_ROOT}/deploy/templates/coturn.conf.template"
  local tmp
  tmp="$(mktemp)"

  local tls_port_line=""
  local tls_cert_block=""
  if [[ "$ENABLE_TLS" == "true" && -f "${CERT_DIR}/fullchain.pem" && -f "${CERT_DIR}/privkey.pem" ]]; then
    tls_port_line="tls-listening-port=${TURNS_PORT}"
    tls_cert_block="cert=${CERT_DIR}/fullchain.pem\npkey=${CERT_DIR}/privkey.pem"
  elif [[ "$ENABLE_TLS" == "true" ]]; then
    log_warn "TLS enabled but cert files missing; coturn TLS listeners will be disabled"
  fi

  render_template "$tpl" "$tmp" \
    DOMAIN "$DOMAIN" \
    TURN_PORT "$TURN_PORT" \
    TLS_PORT_LINE "$tls_port_line" \
    TURN_USER "$TURN_USER" \
    TURN_PASS "$TURN_PASS" \
    TURN_EXTERNAL_IP "$TURN_EXTERNAL_IP" \
    TURN_INTERNAL_IP "$TURN_INTERNAL_IP" \
    TURN_MIN_PORT "$TURN_MIN_PORT" \
    TURN_MAX_PORT "$TURN_MAX_PORT" \
    TLS_CERT_BLOCK "$tls_cert_block"

  run_required "install coturn config" $SUDO cp "$tmp" /etc/turnserver.conf
  run_required "enable coturn" $SUDO sed -i 's/^#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
  run_required "restart coturn" $SUDO systemctl restart coturn
  run_required "enable coturn service" $SUDO systemctl enable coturn

  rm -f "$tmp"
}
