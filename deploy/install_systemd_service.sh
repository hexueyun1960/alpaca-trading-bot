#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/opt/alpaca-bot}"
SERVICE_NAME="${2:-alpaca-bot}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_systemd_service.sh ${APP_DIR} ${SERVICE_NAME}" >&2
  exit 1
fi

if [[ ! -d "${APP_DIR}" ]]; then
  echo "App directory does not exist: ${APP_DIR}" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "Missing ${APP_DIR}/.env" >&2
  exit 1
fi

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  echo "Missing virtualenv Python at ${APP_DIR}/venv/bin/python" >&2
  exit 1
fi

sed "s|/opt/alpaca-bot|${APP_DIR}|g" "${APP_DIR}/deploy/alpaca-bot.service" > "${SERVICE_PATH}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}"
