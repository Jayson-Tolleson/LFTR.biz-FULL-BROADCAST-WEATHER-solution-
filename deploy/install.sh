#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=deploy/lib/common.sh
source "${ROOT_DIR}/deploy/lib/common.sh"
# shellcheck source=deploy/lib/health.sh
source "${ROOT_DIR}/deploy/lib/health.sh"
# shellcheck source=deploy/lib/packages.sh
source "${ROOT_DIR}/deploy/lib/packages.sh"
# shellcheck source=deploy/lib/filesystem.sh
source "${ROOT_DIR}/deploy/lib/filesystem.sh"
# shellcheck source=deploy/lib/python.sh
source "${ROOT_DIR}/deploy/lib/python.sh"
# shellcheck source=deploy/lib/google.sh
source "${ROOT_DIR}/deploy/lib/google.sh"
# shellcheck source=deploy/lib/nginx.sh
source "${ROOT_DIR}/deploy/lib/nginx.sh"
# shellcheck source=deploy/lib/firewall.sh
source "${ROOT_DIR}/deploy/lib/firewall.sh"
# shellcheck source=deploy/lib/certs.sh
source "${ROOT_DIR}/deploy/lib/certs.sh"
# shellcheck source=deploy/lib/coturn.sh
source "${ROOT_DIR}/deploy/lib/coturn.sh"
# shellcheck source=deploy/lib/services.sh
source "${ROOT_DIR}/deploy/lib/services.sh"

APP_USER="${APP_USER:-jayson_tolleson}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_DIR="${APP_DIR:-/home/${APP_USER}/broadcast}"
APP_SERVICE_NAME="${APP_SERVICE_NAME:-broadcast.service}"
APP_BIND_HOST="${APP_BIND_HOST:-127.0.0.1}"
APP_BIND_PORT="${APP_BIND_PORT:-8000}"
APP_WORKERS="${APP_WORKERS:-1}"
DOMAIN="${DOMAIN:-lftr.biz}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@${DOMAIN}}"
TURN_PORT="${TURN_PORT:-3478}"
TURNS_PORT="${TURNS_PORT:-5349}"
TURN_MIN_PORT="${TURN_MIN_PORT:-49160}"
TURN_MAX_PORT="${TURN_MAX_PORT:-49200}"
TURN_USER="${TURN_USER:-webrtc}"
TURN_PASS="${TURN_PASS:-strongpassword}"
GCP_PROJECT_ID="${GCP_PROJECT_ID:-}"
VERTEX_AI_REGION="${VERTEX_AI_REGION:-global}"
MAPS_API_KEY="${MAPS_API_KEY:-}"
GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-${APP_DIR}/keys/gcp-key.json}"

export ROOT_DIR APP_USER APP_GROUP APP_DIR APP_SERVICE_NAME APP_BIND_HOST APP_BIND_PORT APP_WORKERS DOMAIN
export CERTBOT_EMAIL TURN_PORT TURNS_PORT TURN_MIN_PORT TURN_MAX_PORT TURN_USER TURN_PASS
export GCP_PROJECT_ID VERTEX_AI_REGION MAPS_API_KEY GOOGLE_APPLICATION_CREDENTIALS

check_environment() {
  verify_root_privileges
  verify_python_version
  verify_domain_dns
  verify_port_access
}

install_packages() { install_system_packages; }
create_filesystem_layout() { create_filesystem_layout_impl; }
install_python_runtime() { install_python_runtime_impl; }
copy_application_files() { copy_application_files_impl; }
configure_google_apis() { configure_google_apis_impl; }
configure_nginx() { configure_nginx_impl; }
verify_firewall_ports() { verify_firewall_ports_impl; }
issue_tls_certificate() { issue_tls_certificate_impl; }
configure_coturn() { configure_coturn_impl; }
create_systemd_services() { create_systemd_services_impl; }
verify_installation() { verify_installation_impl; }

main() {
  log_info "Starting broadcast installer (idempotent mode)"
  local steps=(
    check_environment
    install_packages
    create_filesystem_layout
    install_python_runtime
    copy_application_files
    configure_google_apis
    configure_nginx
    verify_firewall_ports
    issue_tls_certificate
    configure_coturn
    create_systemd_services
    verify_installation
  )
  for step in "${steps[@]}"; do
    run_required "$step" "$step"
  done
  log_ok "Installation complete"
}

main "$@"
