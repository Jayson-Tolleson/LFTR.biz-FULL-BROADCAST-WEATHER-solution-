#!/usr/bin/env bash
set -euo pipefail

certs_ready() {
  [[ -f "${CERT_DIR}/fullchain.pem" && -f "${CERT_DIR}/privkey.pem" ]]
}

issue_or_refresh_certs() {
  [[ "$ENABLE_TLS" == "true" ]] || { log_info "TLS disabled; skipping cert issuance"; return 0; }

  if certs_ready; then
    log_info "TLS certificates already exist at ${CERT_DIR}"
    return 0
  fi

  if ! command -v certbot >/dev/null 2>&1; then
    log_warn "certbot unavailable; TLS cert issuance skipped"
    return 0
  fi

  run_optional "obtain/renew certificate with certbot" \
    $SUDO certbot certonly --standalone --non-interactive --agree-tos \
      --email "$CERTBOT_EMAIL" -d "$DOMAIN"
}
