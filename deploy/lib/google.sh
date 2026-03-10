#!/usr/bin/env bash
set -euo pipefail

configure_google_apis_impl() {
  local google_env="$APP_DIR/config/google.env"
  local maps_key="${MAPS_API_KEY:-}"
  local project_id="${GCP_PROJECT_ID:-}"
  local region="${VERTEX_AI_REGION:-global}"

  if [[ -t 0 ]]; then
    if [[ -z "$maps_key" ]]; then read -r -p "Google Maps API Key (optional): " maps_key || true; fi
    if [[ -z "$project_id" ]]; then read -r -p "GCP Project ID (optional): " project_id || true; fi
    if [[ -z "$region" ]]; then read -r -p "Vertex AI region [global]: " region || true; region="${region:-global}"; fi
  fi

  cat > "$google_env" <<EOF
MAPS_API_KEY=${maps_key}
GOOGLE_MAPS_API_KEY=${maps_key}
GOOGLE_CLOUD_PROJECT=${project_id}
GOOGLE_CLOUD_LOCATION=${region}
GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS}
EOF
  chmod 600 "$google_env"
  chown "$APP_USER:$APP_GROUP" "$google_env"

  if [[ ! -f "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
    log_warn "Optional service account key missing: $GOOGLE_APPLICATION_CREDENTIALS"
  fi
}
