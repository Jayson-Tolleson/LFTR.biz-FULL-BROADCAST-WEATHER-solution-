#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=deploy/lib/common.sh
source "${ROOT_DIR}/deploy/lib/common.sh"
# shellcheck source=deploy/lib/env.sh
source "${ROOT_DIR}/deploy/lib/env.sh"
# shellcheck source=deploy/lib/packages.sh
source "${ROOT_DIR}/deploy/lib/packages.sh"
# shellcheck source=deploy/lib/certs.sh
source "${ROOT_DIR}/deploy/lib/certs.sh"
# shellcheck source=deploy/lib/nginx.sh
source "${ROOT_DIR}/deploy/lib/nginx.sh"
# shellcheck source=deploy/lib/coturn.sh
source "${ROOT_DIR}/deploy/lib/coturn.sh"
# shellcheck source=deploy/lib/services.sh
source "${ROOT_DIR}/deploy/lib/services.sh"
# shellcheck source=deploy/lib/diagnostics.sh
source "${ROOT_DIR}/deploy/lib/diagnostics.sh"
# shellcheck source=deploy/lib/gcp.sh
source "${ROOT_DIR}/deploy/lib/gcp.sh"

ONLY_DIAG=false
for arg in "$@"; do
  case "$arg" in
    --diagnostics) ONLY_DIAG=true ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

load_deploy_env "$ROOT_DIR"
compute_runtime_values
configure_google_maps_api_key

if [[ "$ONLY_DIAG" == "true" ]]; then
  run_diagnostics
  exit 0
fi

log_info "Starting deployment"
log_info "App dir: ${APP_DIR} | Service: ${APP_SERVICE_NAME} | Domain: ${DOMAIN}"

run_required "validate required commands" require_cmd python3
run_required "validate required commands" require_cmd rsync

run_optional "gcp metadata detection" maybe_detect_gcp

install_system_packages
install_app_files
install_python_dependencies
install_socketio_client_asset
install_env_file
issue_or_refresh_certs
install_coturn_config
install_nginx_config
install_systemd_service
run_diagnostics

log_ok "Deployment completed"
printf '\nSummary:\n'
printf '  App dir: %s\n' "$APP_DIR"
printf '  Service: %s\n' "$APP_SERVICE_NAME"
SCHEME="http"
if [[ "$ENABLE_TLS" == "true" && -f "${CERT_DIR}/fullchain.pem" && -f "${CERT_DIR}/privkey.pem" ]]; then
  SCHEME="https"
fi
printf '  Broadcast URL: %s\n' "${SCHEME}://${DOMAIN}/broadcast"
printf '  Watch URL: %s\n' "${SCHEME}://${DOMAIN}/watch"
