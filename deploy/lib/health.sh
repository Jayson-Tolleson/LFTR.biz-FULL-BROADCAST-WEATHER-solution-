#!/usr/bin/env bash
set -euo pipefail

verify_root_privileges() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    die "Please run installer as root: sudo ./broadcast.sh"
  fi
}

verify_python_version() {
  require_cmd python3
  local v
  v="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  log_info "python version=${v}"
}

verify_domain_dns() {
  require_cmd curl
  local server_ip domain_ip
  server_ip="$(curl -fsS ifconfig.me 2>/dev/null || true)"
  if command -v dig >/dev/null 2>&1; then
    domain_ip="$(dig +short "$DOMAIN" | tail -n1)"
  else
    domain_ip="$(getent ahosts "$DOMAIN" 2>/dev/null | awk '/STREAM/ {print $1; exit}')"
  fi
  if [[ -n "$server_ip" && -n "$domain_ip" && "$server_ip" == "$domain_ip" ]]; then
    log_ok "DNS verified for ${DOMAIN} -> ${domain_ip}"
  else
    log_warn "Domain does not resolve to this server (${DOMAIN}: ${domain_ip:-unknown}, server: ${server_ip:-unknown})"
  fi
}

verify_port_access() {
  for p in 80 443 3478 5349; do
    if command -v nc >/dev/null 2>&1; then
      nc -z 127.0.0.1 "$p" >/dev/null 2>&1 || true
    fi
  done
}
