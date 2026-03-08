#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '[INFO] broadcast.sh compatibility wrapper: delegating to deploy/install.sh\n'
exec "${ROOT_DIR}/deploy/install.sh" "$@"
