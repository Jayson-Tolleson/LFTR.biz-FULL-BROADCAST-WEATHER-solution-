#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

prompt_default () {
  local prompt="$1"
  local default="$2"
  local value
  read -r -p "$prompt [$default]: " value
  echo "${value:-$default}"
}

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

echo "===== BROADCAST INSTALLER CONFIGURATION ====="

DEFAULT_IP="$(curl -s ifconfig.me || echo "127.0.0.1")"
DEFAULT_DOMAIN=""
if compgen -G "/etc/nginx/sites-enabled/*" >/dev/null 2>&1; then
  DEFAULT_DOMAIN="$(grep -h "server_name" /etc/nginx/sites-enabled/* 2>/dev/null | awk '{print $2}' | tr -d ';' | head -n1 || true)"
fi
DEFAULT_DOMAIN="${DEFAULT_DOMAIN:-$DEFAULT_IP}"

DEFAULT_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
DEFAULT_PROJECT="${DEFAULT_PROJECT:-my-gcp-project}"

DEFAULT_MAPS_KEY="PASTE_API_KEY_HERE"
if [[ -f "${ROOT_DIR}/static/indexgfs.html" ]]; then
  DETECTED_MAPS_KEY="$(grep -o 'key=[^"& ]*' "${ROOT_DIR}/static/indexgfs.html" 2>/dev/null | head -n1 | cut -d= -f2- || true)"
  if [[ -n "${DETECTED_MAPS_KEY}" ]]; then
    DEFAULT_MAPS_KEY="${DETECTED_MAPS_KEY}"
  fi
fi

DOMAIN="$(prompt_default "Enter domain name (used for nginx + SSL)" "$DEFAULT_DOMAIN")"
GOOGLE_PROJECT_ID="$(prompt_default "Enter Google Cloud Project ID" "$DEFAULT_PROJECT")"
MAPS_API_KEY="$(prompt_default "Enter Google Maps JS API key" "$DEFAULT_MAPS_KEY")"
EMAIL="$(prompt_default "Enter email for Let's Encrypt certificate" "admin@$DOMAIN")"
GCP_KEY="$(prompt_default "Path to GCP service account key" "/etc/broadcast/gcp-key.json")"
VERTEX_LOCATION="$(prompt_default "Vertex AI location" "global")"
VERTEX_MODEL="$(prompt_default "Vertex AI model" "gemini-2.5-flash")"
AI_PROVIDER="$(prompt_default "AI provider" "vertex")"

mkdir -p /etc/broadcast
cat > /etc/broadcast/install.env <<CFG
DOMAIN=$DOMAIN
GOOGLE_PROJECT_ID=$GOOGLE_PROJECT_ID
MAPS_API_KEY=$MAPS_API_KEY
GOOGLE_MAPS_API_KEY=$MAPS_API_KEY
GOOGLE_CLOUD_REGION=global
EMAIL=$EMAIL
GCP_KEY=$GCP_KEY
VERTEX_LOCATION=$VERTEX_LOCATION
VERTEX_MODEL=$VERTEX_MODEL
AI_PROVIDER=$AI_PROVIDER
CFG

export DOMAIN
export GOOGLE_PROJECT_ID
export MAPS_API_KEY
export GOOGLE_MAPS_API_KEY="$MAPS_API_KEY"
export GOOGLE_CLOUD_REGION="global"
export EMAIL
export CERTBOT_EMAIL="$EMAIL"
export GCP_KEY
export VERTEX_LOCATION
export VERTEX_MODEL
export AI_PROVIDER

python3 - <<PYMAPS
from pathlib import Path
import re

root = Path("${ROOT_DIR}")
loader = '<script src="https://maps.googleapis.com/maps/api/js?key=${MAPS_API_KEY}&v=beta&libraries=maps3d,marker"></script>'
pattern = re.compile(r'^\s*<script[^>]*maps\.googleapis\.com/maps/api/js[^>]*></script>\s*$', re.IGNORECASE)

for html in sorted((root / "static").rglob("*.html")):
    text = html.read_text(encoding="utf-8")
    lines = text.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if pattern.search(line):
            lines[i] = loader
            replaced = True
    if not replaced:
        updated = "\n".join(lines)
        if "</head>" in updated:
            updated = updated.replace("</head>", f"{loader}\n</head>", 1)
            html.write_text(updated, encoding="utf-8")
            continue
        else:
            lines.insert(0, loader)
    html.write_text("\n".join(lines), encoding="utf-8")
PYMAPS

echo "[INFO] Injected Google Maps API key into HTML files"

if [[ "$DOMAIN" == "$DEFAULT_IP" ]]; then
  echo "[WARN] Domain matches public IP; SSL request will be skipped by installer."
  export SKIP_SSL=1
fi

echo "===== INSTALL CONFIGURATION ====="
echo "Domain: $DOMAIN"
echo "Project: $GOOGLE_PROJECT_ID"
echo "Maps API: configured"
if [[ -f "$GCP_KEY" ]]; then
  echo "GCP key: $GCP_KEY"
else
  echo "GCP key: (not set; ADC mode)"
fi

exec "${ROOT_DIR}/deploy/install.sh" "$@"
