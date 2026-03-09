#!/usr/bin/env bash
set -euo pipefail

install_app_files() {
  run_required "create app directory" $SUDO mkdir -p "$APP_DIR"
  run_required "sync app files" $SUDO rsync -a --delete \
    --exclude '.git/' \
    --exclude 'deploy/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    "$REPO_ROOT/" "$APP_DIR/"
  run_required "set app ownership" $SUDO chown -R "${APP_USER}:${APP_GROUP}" "$APP_DIR"
  run_required "verify fish location CSV" test -f "${APP_DIR}/static/data/fishloclist.csv"
}

install_env_file() {
  local tpl="${REPO_ROOT}/deploy/templates/app.env.template"
  local tmp
  tmp="$(mktemp)"

  render_template "$tpl" "$tmp" \
    DOMAIN "$DOMAIN" \
    APP_BIND_HOST "$APP_BIND_HOST" \
    APP_BIND_PORT "$APP_BIND_PORT" \
    TURN_USER "$TURN_USER" \
    TURN_PASS "$TURN_PASS" \
    TURN_URL "turn:${DOMAIN}:${TURN_PORT}" \
    TURNS_URL "turns:${DOMAIN}:${TURNS_PORT}" \
    GOOGLE_MAPS_API_KEY "${GOOGLE_MAPS_API_KEY:-}"

  run_required "ensure env directory" $SUDO mkdir -p "$(dirname "$APP_ENV_FILE")"
  run_required "install app env file" $SUDO cp "$tmp" "$APP_ENV_FILE"
  if [[ -n "${GOOGLE_MAPS_API_KEY:-}" ]]; then
    set_env_value "$tmp" "GOOGLE_MAPS_API_KEY" "$GOOGLE_MAPS_API_KEY"
    run_required "persist GOOGLE_MAPS_API_KEY" $SUDO cp "$tmp" "$APP_ENV_FILE"
    log_info "Saved GOOGLE_MAPS_API_KEY=$(mask_secret_value "$GOOGLE_MAPS_API_KEY")"
  elif [[ -f "$APP_ENV_FILE" ]]; then
    local existing
    existing="$(read_env_value_from_file "$APP_ENV_FILE" "GOOGLE_MAPS_API_KEY" || true)"
    if [[ -n "$existing" ]]; then
      log_info "Keeping existing GOOGLE_MAPS_API_KEY=$(mask_secret_value "$existing")"
    fi
  fi
  run_required "secure env file permissions" $SUDO chmod 600 "$APP_ENV_FILE"
  run_required "set env file ownership" $SUDO chown "${APP_USER}:${APP_GROUP}" "$APP_ENV_FILE"

  rm -f "$tmp"
}

install_systemd_service() {
  local tpl="${REPO_ROOT}/deploy/templates/app.service.template"
  local tmp
  tmp="$(mktemp)"

  render_template "$tpl" "$tmp" \
    APP_SERVICE_NAME "$APP_SERVICE_NAME" \
    APP_USER "$APP_USER" \
    APP_GROUP "$APP_GROUP" \
    APP_DIR "$APP_DIR" \
    APP_ENV_FILE "$APP_ENV_FILE" \
    APP_BIND_HOST "$APP_BIND_HOST" \
    APP_BIND_PORT "$APP_BIND_PORT" \
    APP_WORKERS "$APP_WORKERS"

  run_required "install systemd unit" $SUDO cp "$tmp" "$SYSTEMD_UNIT_PATH"
  run_required "systemd daemon-reload" $SUDO systemctl daemon-reload
  run_required "enable app service" $SUDO systemctl enable "$APP_SERVICE_NAME"
  run_required "restart app service" $SUDO systemctl restart "$APP_SERVICE_NAME"

  rm -f "$tmp"
}
