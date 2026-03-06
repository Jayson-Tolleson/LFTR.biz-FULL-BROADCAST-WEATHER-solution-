#!/usr/bin/env bash
set -euo pipefail

install_system_packages() {
  [[ "$INSTALL_SYSTEM_PACKAGES" == "true" ]] || { log_info "Skipping system package installation"; return 0; }
  run_required "apt-get update" $SUDO apt-get update -y
  run_required "install system packages" $SUDO apt-get install -y \
    python3 python3-venv python3-pip nginx certbot python3-certbot-nginx \
    coturn ffmpeg curl rsync unzip zip
}

install_python_dependencies() {
  [[ "$INSTALL_PY_DEPS" == "true" ]] || { log_info "Skipping python dependency installation"; return 0; }
  [[ -f "${APP_DIR}/requirements.txt" ]] || { log_warn "requirements.txt missing; skipping pip install"; return 0; }

  run_required "create venv" python3 -m venv "${APP_DIR}/venv"
  run_required "upgrade pip" "${APP_DIR}/venv/bin/pip" install --progress-bar off --upgrade pip setuptools wheel
  run_required "install requirements" "${APP_DIR}/venv/bin/pip" install --progress-bar off -r "${APP_DIR}/requirements.txt"
}
