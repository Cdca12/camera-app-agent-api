#!/usr/bin/env bash
# Update a clean agent checkout safely and restore code/data if health fails.
# Run with sudo on the Raspberry Pi. The configured credentials are never echoed.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="/var/lib/cameraapp"
BACKUP_DIR="/var/backups/cameraapp"
DATABASE_PATH="${CAMERA_APP_DB_PATH:-${STATE_DIR}/camera_app_operational.db}"
DATABASE_KEY_PATH="${DATABASE_PATH}.key"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"
PREVIOUS_REVISION=""

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta este actualizador con sudo."
  exit 1
fi

restore_previous_state() {
  echo "La actualización no superó el health check. Restaurando versión y datos..." >&2
  if [[ -n "${PREVIOUS_REVISION}" ]]; then
    git -C "${APP_DIR}" reset --hard "${PREVIOUS_REVISION}"
  fi
  if [[ -f "${BACKUP_PATH}/camera_app_operational.db" ]]; then
    install -m 600 "${BACKUP_PATH}/camera_app_operational.db" "${DATABASE_PATH}"
  fi
  if [[ -f "${BACKUP_PATH}/camera_app_operational.db.key" ]]; then
    install -m 600 "${BACKUP_PATH}/camera_app_operational.db.key" "${DATABASE_KEY_PATH}"
  fi
  systemctl restart cameraapp-agent || true
}

trap restore_previous_state ERR

if [[ -n "$(git -C "${APP_DIR}" status --porcelain)" ]]; then
  echo "El checkout tiene cambios locales. Detén la actualización y revísalos antes."
  exit 1
fi

PREVIOUS_REVISION="$(git -C "${APP_DIR}" rev-parse HEAD)"
mkdir -p "${BACKUP_PATH}"
chmod 700 "${BACKUP_DIR}" "${BACKUP_PATH}"

if [[ -f "${DATABASE_PATH}" ]]; then
  install -m 600 "${DATABASE_PATH}" "${BACKUP_PATH}/camera_app_operational.db"
fi
if [[ -f "${DATABASE_KEY_PATH}" ]]; then
  install -m 600 "${DATABASE_KEY_PATH}" "${BACKUP_PATH}/camera_app_operational.db.key"
fi

git -C "${APP_DIR}" pull --ff-only
"${APP_DIR}/scripts/install_agent.sh"

for _ in {1..12}; do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:7860/health >/dev/null; then
    trap - ERR
    echo "Actualización exitosa. Respaldo local: ${BACKUP_PATH}"
    exit 0
  fi
  sleep 5
done

echo "El agente no respondió al health check." >&2
exit 1
