#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib/google.sh"
mode="$(resolve_google_credentials)"
echo "google_auth_mode=${mode}"
