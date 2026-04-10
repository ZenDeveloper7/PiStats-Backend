#!/usr/bin/env bash
set -euo pipefail

APP_NAME="pistats"
DEFAULT_USER="zen"
DEFAULT_INSTALL_DIR="/home/${DEFAULT_USER}/pistats-backend"
DEFAULT_SERVICE_PATH="/etc/systemd/system/${APP_NAME}.service"

usage() {
  cat <<'EOF'
Usage:
  ./install-on-pi.sh [options]

Options:
  --user USER              Linux user that will run the service. Default: zen
  --install-dir PATH       Install directory on the Pi. Default: /home/zen/pistats-backend
  --service-name NAME      systemd service name without .service. Default: pistats
  --port PORT              Preferred starting port. Installer will move upward until it finds a free port. Default: 8787
  --bind-mode MODE         One of: localhost, tailscale, custom. Default: tailscale
  --tailscale-ip IP        Optional explicit Tailscale IP written to .env
  --backup-label LABEL     Backup label written to .env. Default: PiBackup
  --services CSV           Docker services list written to .env
  --token TOKEN            Auth token written to .env if no .env exists yet
  --force-env              Overwrite an existing .env file
  --no-start               Install files but do not enable/start the service
  -h, --help               Show this help

Examples:
  sudo ./install-on-pi.sh --token 'replace-me'
  sudo ./install-on-pi.sh --user zen --bind-mode tailscale --port 8788 --token 'replace-me'
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This installer needs root so it can write the systemd unit." >&2
    echo "Run it with sudo." >&2
    exit 1
  fi
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

is_port_in_use() {
  local port="$1"

  if command_exists ss; then
    ss -ltn "( sport = :${port} )" 2>/dev/null | tail -n +2 | grep -q .
    return $?
  fi

  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    raise SystemExit(0)
finally:
    try:
        sock.close()
    except OSError:
        pass
raise SystemExit(1)
PY
}

find_available_port() {
  local start_port="$1"
  local port="$start_port"
  local max_port=65535

  while [[ "${port}" -le "${max_port}" ]]; do
    if ! is_port_in_use "${port}"; then
      echo "${port}"
      return 0
    fi
    port=$((port + 1))
  done

  echo "Could not find a free TCP port starting from ${start_port}" >&2
  exit 1
}

SERVICE_NAME="${APP_NAME}"
SERVICE_USER="${DEFAULT_USER}"
INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
PORT="8787"
BIND_MODE="tailscale"
TAILSCALE_IP=""
BACKUP_LABEL="PiBackup"
SERVICES_CSV="vaultwarden,trilium,samba,pihole"
TOKEN=""
FORCE_ENV="0"
START_SERVICE="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      SERVICE_USER="$2"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --bind-mode)
      BIND_MODE="$2"
      shift 2
      ;;
    --tailscale-ip)
      TAILSCALE_IP="$2"
      shift 2
      ;;
    --backup-label)
      BACKUP_LABEL="$2"
      shift 2
      ;;
    --services)
      SERVICES_CSV="$2"
      shift 2
      ;;
    --token)
      TOKEN="$2"
      shift 2
      ;;
    --force-env)
      FORCE_ENV="1"
      shift
      ;;
    --no-start)
      START_SERVICE="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_PATH="${INSTALL_DIR}/.env"

require_root

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "User ${SERVICE_USER} does not exist." >&2
  exit 1
fi

if [[ ! -f "${PWD}/pi_backend/server.py" ]]; then
  echo "Run this script from the pi-backend directory." >&2
  exit 1
fi

if ! command_exists python3; then
  echo "python3 is required but not installed." >&2
  exit 1
fi

if [[ "${BIND_MODE}" != "localhost" && "${BIND_MODE}" != "tailscale" && "${BIND_MODE}" != "custom" ]]; then
  echo "--bind-mode must be one of: localhost, tailscale, custom" >&2
  exit 1
fi

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || [[ "${PORT}" -lt 1 ]] || [[ "${PORT}" -gt 65535 ]]; then
  echo "--port must be a valid TCP port between 1 and 65535" >&2
  exit 1
fi

REQUESTED_PORT="${PORT}"
PORT="$(find_available_port "${REQUESTED_PORT}")"

echo "Installing PiStats backend"
echo "  user: ${SERVICE_USER}"
echo "  install dir: ${INSTALL_DIR}"
echo "  service: ${SERVICE_NAME}.service"
echo "  bind mode: ${BIND_MODE}"
echo "  requested port: ${REQUESTED_PORT}"
echo "  selected port: ${PORT}"

if [[ "${PORT}" != "${REQUESTED_PORT}" ]]; then
  echo "  note: ${REQUESTED_PORT} was busy, so the installer chose ${PORT}"
fi

mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  ./ "${INSTALL_DIR}/"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod +x "${INSTALL_DIR}/install-on-pi.sh"

if [[ ! -f "${ENV_PATH}" || "${FORCE_ENV}" == "1" ]]; then
  if [[ -z "${TOKEN}" ]]; then
    TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  fi

  cat >"${ENV_PATH}" <<EOF
PISTATS_TOKEN=${TOKEN}
PISTATS_BIND_MODE=${BIND_MODE}
PISTATS_PORT=${PORT}
PISTATS_SERVICES=${SERVICES_CSV}
PISTATS_BACKUP_LABEL=${BACKUP_LABEL}
EOF

  if [[ -n "${TAILSCALE_IP}" ]]; then
    cat >>"${ENV_PATH}" <<EOF
PISTATS_TAILSCALE_IP=${TAILSCALE_IP}
EOF
  fi

  chown "${SERVICE_USER}:${SERVICE_USER}" "${ENV_PATH}"
  chmod 600 "${ENV_PATH}"
  echo "Wrote ${ENV_PATH}"
else
  echo "Keeping existing ${ENV_PATH}"
fi

cat >"${SERVICE_PATH}" <<EOF
[Unit]
Description=PiStats Raspberry Pi monitoring backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_PATH}
ExecStart=/usr/bin/python3 -m pi_backend.server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "${SERVICE_PATH}"
systemctl daemon-reload

if [[ "${START_SERVICE}" == "1" ]]; then
  systemctl enable --now "${SERVICE_NAME}.service"
  systemctl status "${SERVICE_NAME}.service" --no-pager || true
else
  echo "Installed service unit at ${SERVICE_PATH} without starting it."
fi

echo
echo "PiStats install complete."
echo "Config file: ${ENV_PATH}"
echo "Service: ${SERVICE_NAME}.service"
echo "Port: ${PORT}"

if [[ "${BIND_MODE}" == "tailscale" ]]; then
  echo
  echo "Next checks:"
  echo "  tailscale ip -4"
  echo "  curl -H \"Authorization: Bearer <token>\" http://<tailscale-ip>:${PORT}/api/stats"
fi
