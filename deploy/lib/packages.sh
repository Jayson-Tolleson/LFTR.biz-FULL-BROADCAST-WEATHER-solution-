#!/usr/bin/env bash
set -euo pipefail

install_system_packages() {
  [[ "$INSTALL_SYSTEM_PACKAGES" == "true" ]] || { log_info "Skipping system package installation"; return 0; }
  run_required "apt-get update" $SUDO apt-get update -y
  run_required "install system packages" $SUDO apt-get install -y \
    python3 python3-venv python3-pip nginx certbot python3-certbot-nginx \
    coturn ffmpeg curl rsync unzip zip nodejs npm
}

install_python_dependencies() {
  [[ "$INSTALL_PY_DEPS" == "true" ]] || { log_info "Skipping python dependency installation"; return 0; }
  [[ -f "${APP_DIR}/requirements.txt" ]] || die "requirements.txt missing at ${APP_DIR}; cannot install python dependencies"

  run_required "create venv" python3 -m venv "${APP_DIR}/venv"
  run_required "upgrade pip" "${APP_DIR}/venv/bin/pip" install --progress-bar off --upgrade pip setuptools wheel
  run_required "install requirements" "${APP_DIR}/venv/bin/pip" install --progress-bar off -r "${APP_DIR}/requirements.txt"
}


install_socketio_client_asset() {
  [[ -d "${APP_DIR}" ]] || die "APP_DIR does not exist: ${APP_DIR}"
  [[ -d "${APP_DIR}/static" ]] || die "static directory missing at ${APP_DIR}/static"

  run_required "validate required commands" require_cmd npm
  run_required "ensure static vendor directory" mkdir -p "${APP_DIR}/static/vendor"
  run_required "install socket.io-client" bash -lc "cd \"${APP_DIR}\" && npm install socket.io-client --no-save"

  local src_js="${APP_DIR}/node_modules/socket.io-client/dist/socket.io.js"
  local src_min_js="${APP_DIR}/node_modules/socket.io-client/dist/socket.io.min.js"
  local src=""

  if [[ -f "$src_js" ]]; then
    src="$src_js"
  elif [[ -f "$src_min_js" ]]; then
    src="$src_min_js"
  else
    die "socket.io client build not found after npm install (expected socket.io.js or socket.io.min.js)"
  fi

  run_required "install socket.io browser asset" cp "$src" "${APP_DIR}/static/vendor/socket.io.js"
}
