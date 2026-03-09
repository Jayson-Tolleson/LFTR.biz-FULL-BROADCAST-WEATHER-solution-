#!/usr/bin/env bash
set -euo pipefail

create_filesystem_layout_impl() {
  mkdir -p "$APP_DIR" \
    "$APP_DIR/config" \
    "$APP_DIR/logs" \
    "$APP_DIR/uploads" \
    "$APP_DIR/uploads/video" \
    "$APP_DIR/uploads/thumbs" \
    "$APP_DIR/keys" \
    "$APP_DIR/static"
  chmod -R 755 "$APP_DIR"
}

copy_application_files_impl() {
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    "$ROOT_DIR/" "$APP_DIR/"
  mkdir -p "$APP_DIR/uploads/video" "$APP_DIR/uploads/thumbs" "$APP_DIR/logs" "$APP_DIR/config" "$APP_DIR/keys"
  chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
}
