#!/usr/bin/env bash
# Install the checked-out CameraApp agent as the Raspberry Pi system service.
# Run with sudo on the Raspberry Pi; it never prints values from agent.env.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAMERAAPP_USER="${SUDO_USER:-cameraapp}"
STATE_DIR="/var/lib/cameraapp"
CONFIG_DIR="/etc/cameraapp"
SERVICE_PATH="/etc/systemd/system/cameraapp-agent.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta este instalador con sudo."
  exit 1
fi

if ! id "${CAMERAAPP_USER}" >/dev/null 2>&1; then
  echo "No existe el usuario operativo '${CAMERAAPP_USER}'."
  exit 1
fi

mkdir -p "${STATE_DIR}" "${STATE_DIR}/models" "${STATE_DIR}/tmp" "${CONFIG_DIR}"
chown -R "${CAMERAAPP_USER}:${CAMERAAPP_USER}" "${STATE_DIR}"
chmod 700 "${STATE_DIR}" "${STATE_DIR}/models" "${STATE_DIR}/tmp"

if [[ ! -f "${CONFIG_DIR}/agent.env" ]]; then
  install -m 600 -o "${CAMERAAPP_USER}" -g "${CAMERAAPP_USER}" \
    "${APP_DIR}/.env.example" "${CONFIG_DIR}/agent.env"
  echo "Se creó ${CONFIG_DIR}/agent.env. Agrega la configuración real antes de operar el agente."
fi

sed \
  -e "s|@APP_DIR@|${APP_DIR}|g" \
  -e "s|@CAMERAAPP_USER@|${CAMERAAPP_USER}|g" \
  "${APP_DIR}/deploy/systemd/cameraapp-agent.service.template" > "${SERVICE_PATH}"
chmod 644 "${SERVICE_PATH}"

export TMPDIR="${STATE_DIR}/tmp"
"${APP_DIR}/.venv/bin/python" -m pip install --no-cache-dir -r "${APP_DIR}/requirements.txt"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/scripts/install_lightweight_model.py" \
  --model-dir "${STATE_DIR}/models"

systemctl daemon-reload
systemctl enable cameraapp-agent
systemctl restart cameraapp-agent

echo "Servicio instalado. Comprueba: systemctl status cameraapp-agent"
echo "Health local: curl -fsS http://127.0.0.1:7860/health"
