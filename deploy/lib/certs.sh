#!/usr/bin/env bash
set -euo pipefail

issue_tls_certificate_impl() {
  systemctl stop nginx || true
  certbot certonly \
    --standalone \
    --agree-tos \
    --email "$CERTBOT_EMAIL" \
    -d "$DOMAIN" \
    --non-interactive || log_warn "certbot issuance failed (continuing)"
  systemctl start nginx || true

  if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    log_ok "TLS certificate present for ${DOMAIN}"
  else
    log_warn "TLS certificate missing for ${DOMAIN}"
  fi

  certbot renew --dry-run || log_warn "certbot dry-run renewal failed"
}
