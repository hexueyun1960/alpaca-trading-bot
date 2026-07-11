#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/opt/alpaca-bot}"
SERVICE_NAME="${2:-alpaca-bot}"

cd "${APP_DIR}"

echo "== doctor =="
"${APP_DIR}/venv/bin/python" -m src.doctor

echo
echo "== systemd service =="
systemctl --no-pager --full status "${SERVICE_NAME}" || true

echo
echo "== recent journal events =="
JOURNAL_PATH="$(grep -E '^ALPACA_JOURNAL_PATH=' .env | tail -n 1 | cut -d= -f2-)"
JOURNAL_PATH="${JOURNAL_PATH:-${APP_DIR}/logs/trade_journal.jsonl}"
if [[ -f "${JOURNAL_PATH}" ]]; then
  tail -n 20 "${JOURNAL_PATH}"
else
  echo "No trade journal found at ${JOURNAL_PATH}"
fi

echo
echo "== service logs =="
journalctl -u "${SERVICE_NAME}" -n 80 --no-pager || true
