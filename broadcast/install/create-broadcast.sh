#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p \
  "$ROOT_DIR/install" \
  "$ROOT_DIR/server" \
  "$ROOT_DIR/static" \
  "$ROOT_DIR/data" \
  "$ROOT_DIR/uploads/video" \
  "$ROOT_DIR/templates"

if [[ ! -f "$ROOT_DIR/requirements.txt" ]]; then
  echo "requirements.txt not found in $ROOT_DIR"
  exit 1
fi

python3 -m venv "$ROOT_DIR/.venv"
source "$ROOT_DIR/.venv/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT_DIR/requirements.txt"

read -p "Enter Google Maps API Key: " GMAPS_KEY

cat > "$ROOT_DIR/.env" <<ENV
GOOGLE_MAPS_API_KEY=$GMAPS_KEY
PORT=8000
ENV

echo "Starting Quart server on 0.0.0.0:8000"
PYTHONPATH="$ROOT_DIR" python3 -m server.app
