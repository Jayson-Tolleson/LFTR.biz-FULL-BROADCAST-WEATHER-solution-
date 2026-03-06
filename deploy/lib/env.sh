#!/usr/bin/env bash
set -euo pipefail

load_deploy_env() {
  local root_dir="$1"
  local env_file_default="${root_dir}/deploy/.env"
  DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-$env_file_default}"

  if [[ -f "$DEPLOY_ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$DEPLOY_ENV_FILE"; set +a
  fi

  APP_USER="${APP_USER:-${SUDO_USER:-${USER:-broadcast}}}"
  APP_GROUP="${APP_GROUP:-$APP_USER}"
  APP_DIR="${APP_DIR:-/home/${APP_USER}/broadcast}"
  APP_SERVICE_NAME="${APP_SERVICE_NAME:-broadcast.service}"
  APP_BIND_HOST="${APP_BIND_HOST:-127.0.0.1}"
  APP_BIND_PORT="${APP_BIND_PORT:-8000}"
  APP_WORKERS="${APP_WORKERS:-1}"

  DOMAIN="${DOMAIN:-lftr.biz}"
  CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@${DOMAIN}}"
  ENABLE_TLS="${ENABLE_TLS:-true}"
  ENABLE_COTURN="${ENABLE_COTURN:-true}"

  TURN_PORT="${TURN_PORT:-3478}"
  TURNS_PORT="${TURNS_PORT:-5349}"
  TURN_MIN_PORT="${TURN_MIN_PORT:-49160}"
  TURN_MAX_PORT="${TURN_MAX_PORT:-49200}"
  TURN_USER="${TURN_USER:-webrtc}"
  TURN_PASS="${TURN_PASS:-}"
  TURN_EXTERNAL_IP="${TURN_EXTERNAL_IP:-}"
  TURN_INTERNAL_IP="${TURN_INTERNAL_IP:-}"

  INSTALL_SYSTEM_PACKAGES="${INSTALL_SYSTEM_PACKAGES:-true}"
  INSTALL_PY_DEPS="${INSTALL_PY_DEPS:-true}"
  ENABLE_NGINX="${ENABLE_NGINX:-true}"

  REPO_ROOT="$root_dir"
  DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-${APP_DIR}/.deploy}"
  APP_ENV_FILE="${APP_ENV_FILE:-${APP_DIR}/.env}"
  NGINX_SITE_NAME="${NGINX_SITE_NAME:-broadcast}"

  [[ -n "$APP_DIR" ]] || die "APP_DIR must not be empty"
  [[ -n "$APP_SERVICE_NAME" ]] || die "APP_SERVICE_NAME must not be empty"
  [[ -n "$DOMAIN" ]] || die "DOMAIN must not be empty"
}

compute_runtime_values() {
  CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
  NGINX_SITE_AVAILABLE="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
  NGINX_SITE_ENABLED="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
  SYSTEMD_UNIT_PATH="/etc/systemd/system/${APP_SERVICE_NAME}"

  if [[ -z "$TURN_PASS" ]]; then
    TURN_PASS="strongpassword"
  fi

  if [[ -z "$TURN_EXTERNAL_IP" ]]; then
    TURN_EXTERNAL_IP="$(curl -fsS ifconfig.me 2>/dev/null || true)"
  fi

  if [[ -z "$TURN_INTERNAL_IP" ]]; then
    TURN_INTERNAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
}
