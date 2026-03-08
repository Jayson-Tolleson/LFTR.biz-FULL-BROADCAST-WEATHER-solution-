#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_USER="${SUDO_USER:-$USER}"
APP_HOME="/home/$APP_USER"
APP_DIR="$APP_HOME/broadcast"

if [[ ! -d "$APP_HOME" ]]; then
  APP_HOME="$HOME"
  APP_DIR="$APP_HOME/broadcast"
fi

echo "Installing Broadcast Weather System"

echo "Installing system dependencies"
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx rsync

echo "Creating runtime directories"
mkdir -p "$APP_DIR"
mkdir -p "$APP_DIR/logs"
mkdir -p "$APP_DIR/uploads"
mkdir -p "$APP_DIR/uploads/video/locations"
mkdir -p "$APP_DIR/data"

echo "Copying application files"
rsync -av --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  "$REPO_ROOT/broadcast/" "$APP_DIR/"

cd "$APP_DIR"

echo "Creating python environment"
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo "Setting permissions"
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "Installation successful"
echo "Run server with:"
echo "source $APP_DIR/.venv/bin/activate && python server/app.py"
echo "Open:"
echo "http://SERVER_IP/static/indexgfs.html"
echo "http://SERVER_IP/static/broadcast.html"
