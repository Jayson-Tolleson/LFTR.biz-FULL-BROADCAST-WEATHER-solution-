#!/usr/bin/env bash
set -euo pipefail

resolve_google_credentials() {
  local key="${GCP_KEY:-${GOOGLE_APPLICATION_CREDENTIALS:-}}"
  if [[ -n "${key}" && -f "${key}" ]]; then
    export GOOGLE_APPLICATION_CREDENTIALS="${key}"
    echo "explicit_key_ok"
    return 0
  fi
  unset GOOGLE_APPLICATION_CREDENTIALS || true
  echo "adc_ok"
}
