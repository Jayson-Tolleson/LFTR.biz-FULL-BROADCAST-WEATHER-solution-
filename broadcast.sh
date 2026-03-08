#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting Broadcast Weather installer"

chmod +x "$REPO_ROOT/broadcast/install/create-broadcast.sh"

"$REPO_ROOT/broadcast/install/create-broadcast.sh"
