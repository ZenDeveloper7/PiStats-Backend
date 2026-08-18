#!/usr/bin/env bash
set -euo pipefail

APP_NAME="pistats"
DEFAULT_USER="${SUDO_USER:-}"
if [[ "${DEFAULT_USER}" == "root" ]]; then
  DEFAULT_USER=""
fi
DEFAULT_INSTALL_DIR="/opt/pistats"

usage() {
  cat <<'EOF'
Usage:
  ./install-on-pi.sh [options]

Options:
  --user USER              Linux user that runs the service. Default: user invoking sudo
  --install-dir PATH       Install directory on the Pi. Default: /opt/pistats
  --service-name NAME      systemd service name without .service. Default: pistats
  --port PORT              Preferred starting port. Installer will move upward until it finds a free port. Default: 8787
  --bind-mode MODE         One of: localhost, tailscale, custom. Default: localhost
  --host ADDRESS           Bind address used with --bind-mode custom
  --tailscale-ip IP        Optional explicit Tailscale IP written to .env
  --backup-label LABEL     Optional preferred backup-drive filesystem label
  --media-backup-root PATH Enable media backup and store files under PATH
  --media-max-bytes BYTES  Maximum media upload size. Default: 1073741824
  --media-read-timeout SECONDS
                           Maximum pause while reading an upload. Default: 300
  --wake-mac MAC           Optional PC MAC address enabling Wake-on-LAN
  --wake-broadcast IP      LAN broadcast address for Wake-on-LAN. Default: 192.168.1.255
  --wake-port PORT         UDP port for Wake-on-LAN. Default: 9
  --token TOKEN            Optional auth token. If omitted, installer generates one.
  --force-env              Replace .env; generates a new token unless --token is passed
  --no-start               Install files but do not enable/start the service
  -h, --help               Show this help

Examples:
  sudo ./install-on-pi.sh
  sudo ./install-on-pi.sh --user pi --bind-mode tailscale --port 8788
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
BIND_MODE="localhost"
HOST=""
TAILSCALE_IP=""
BACKUP_LABEL=""
MEDIA_BACKUP_ROOT=""
MEDIA_MAX_BYTES="1073741824"
MEDIA_READ_TIMEOUT="300"
WAKE_MAC=""
WAKE_BROADCAST="192.168.1.255"
WAKE_PORT="9"
TOKEN=""
FORCE_ENV="0"
START_SERVICE="1"
USER_EXPLICIT="0"
INSTALL_DIR_EXPLICIT="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user|--install-dir|--service-name|--port|--bind-mode|--host|--tailscale-ip|--backup-label|--media-backup-root|--media-max-bytes|--media-read-timeout|--wake-mac|--wake-broadcast|--wake-port|--token)
      if [[ $# -lt 2 ]]; then
        echo "$1 requires a value" >&2
        exit 1
      fi
      ;;
  esac
  case "$1" in
    --user)
      SERVICE_USER="$2"
      USER_EXPLICIT="1"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      INSTALL_DIR_EXPLICIT="1"
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
    --host)
      HOST="$2"
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
    --media-backup-root)
      MEDIA_BACKUP_ROOT="$2"
      shift 2
      ;;
    --media-max-bytes)
      MEDIA_MAX_BYTES="$2"
      shift 2
      ;;
    --media-read-timeout)
      MEDIA_READ_TIMEOUT="$2"
      shift 2
      ;;
    --wake-mac)
      WAKE_MAC="$2"
      shift 2
      ;;
    --wake-broadcast)
      WAKE_BROADCAST="$2"
      shift 2
      ;;
    --wake-port)
      WAKE_PORT="$2"
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

require_root

SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
if [[ -f "${SERVICE_PATH}" ]]; then
  if [[ "${USER_EXPLICIT}" != "1" ]]; then
    installed_user="$(sed -n 's/^User=//p' "${SERVICE_PATH}" | tail -n 1)"
    if [[ -n "${installed_user}" ]]; then
      SERVICE_USER="${installed_user}"
    fi
  fi
  if [[ "${INSTALL_DIR_EXPLICIT}" != "1" ]]; then
    installed_dir="$(sed -n 's/^WorkingDirectory=//p' "${SERVICE_PATH}" | tail -n 1)"
    if [[ -n "${installed_dir}" ]]; then
      INSTALL_DIR="${installed_dir}"
    fi
  fi
fi
ENV_PATH="${INSTALL_DIR}/.env"

if [[ -z "${SERVICE_USER}" ]]; then
  echo "Could not determine the service user. Pass --user USER." >&2
  exit 1
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "User ${SERVICE_USER} does not exist." >&2
  exit 1
fi

if [[ ! -f "${PWD}/pi_backend/server.py" ]]; then
  echo "Run this script from the PiStats-Backend repository root." >&2
  exit 1
fi

if ! command_exists python3; then
  echo "python3 is required but not installed." >&2
  exit 1
fi

if ! command_exists rsync; then
  echo "rsync is required but not installed." >&2
  exit 1
fi

if ! command_exists realpath; then
  echo "realpath is required but not installed (normally provided by coreutils)." >&2
  exit 1
fi

if ! command_exists runuser; then
  echo "runuser is required but not installed (normally provided by util-linux)." >&2
  exit 1
fi

if [[ "${INSTALL_DIR}" != /* || "${INSTALL_DIR}" == "/" ]]; then
  echo "--install-dir must be an absolute path other than /" >&2
  exit 1
fi
INSTALL_DIR="$(realpath -m -- "${INSTALL_DIR}")"
ENV_PATH="${INSTALL_DIR}/.env"
WAKE_STATE_FILE="${INSTALL_DIR}/state/wake-on-lan.json"

if ! [[ "${SERVICE_NAME}" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
  echo "--service-name contains unsupported characters" >&2
  exit 1
fi

if [[ "${BIND_MODE}" != "localhost" && "${BIND_MODE}" != "tailscale" && "${BIND_MODE}" != "custom" ]]; then
  echo "--bind-mode must be one of: localhost, tailscale, custom" >&2
  exit 1
fi

if [[ "${BIND_MODE}" == "custom" && -z "${HOST}" ]]; then
  echo "--bind-mode custom requires --host ADDRESS" >&2
  exit 1
fi

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || [[ "${PORT}" -lt 1 ]] || [[ "${PORT}" -gt 65535 ]]; then
  echo "--port must be a valid TCP port between 1 and 65535" >&2
  exit 1
fi

if ! [[ "${WAKE_PORT}" =~ ^[0-9]+$ ]] || [[ "${WAKE_PORT}" -lt 1 ]] || [[ "${WAKE_PORT}" -gt 65535 ]]; then
  echo "--wake-port must be a valid UDP port between 1 and 65535" >&2
  exit 1
fi

if ! [[ "${MEDIA_MAX_BYTES}" =~ ^[0-9]+$ ]] || [[ "${MEDIA_MAX_BYTES}" -lt 1 ]]; then
  echo "--media-max-bytes must be a positive integer" >&2
  exit 1
fi

if ! [[ "${MEDIA_READ_TIMEOUT}" =~ ^[0-9]+$ ]] || [[ "${MEDIA_READ_TIMEOUT}" -lt 1 ]]; then
  echo "--media-read-timeout must be a positive integer" >&2
  exit 1
fi

if [[ -n "${MEDIA_BACKUP_ROOT}" && "${MEDIA_BACKUP_ROOT}" != /* ]]; then
  echo "--media-backup-root must be an absolute path" >&2
  exit 1
fi

if [[ -n "${MEDIA_BACKUP_ROOT}" ]]; then
  MEDIA_BACKUP_ROOT="$(realpath -m -- "${MEDIA_BACKUP_ROOT}")"
  case "${MEDIA_BACKUP_ROOT%/}/" in
    "${INSTALL_DIR%/}/"*)
      echo "--media-backup-root must be outside --install-dir" >&2
      exit 1
      ;;
  esac
fi

REQUESTED_PORT="${PORT}"
PRESERVING_ENV="0"
if [[ -f "${ENV_PATH}" && "${FORCE_ENV}" != "1" ]]; then
  PRESERVING_ENV="1"
  configured_bind_mode="$(sed -n 's/^PISTATS_BIND_MODE=//p' "${ENV_PATH}" | tail -n 1)"
  if [[ -n "${configured_bind_mode}" ]]; then
    BIND_MODE="${configured_bind_mode}"
  fi
  configured_port="$(sed -n 's/^PISTATS_PORT=//p' "${ENV_PATH}" | tail -n 1)"
  if [[ "${configured_port}" =~ ^[0-9]+$ ]] && \
     [[ "${configured_port}" -ge 1 ]] && [[ "${configured_port}" -le 65535 ]]; then
    PORT="${configured_port}"
  fi
  configured_wake_state_file="$(sed -n 's/^PISTATS_WAKE_STATE_FILE=//p' "${ENV_PATH}" | tail -n 1)"
  if [[ -n "${configured_wake_state_file}" ]]; then
    WAKE_STATE_FILE="${configured_wake_state_file}"
  fi
else
  PORT="$(find_available_port "${REQUESTED_PORT}")"
fi

if [[ "${WAKE_STATE_FILE}" != /* ]]; then
  WAKE_STATE_FILE="${INSTALL_DIR}/${WAKE_STATE_FILE}"
fi
WAKE_STATE_FILE="$(realpath -m -- "${WAKE_STATE_FILE}")"
WAKE_STATE_DIR="$(dirname -- "${WAKE_STATE_FILE}")"

echo "Installing PiStats backend"
echo "  user: ${SERVICE_USER}"
echo "  install dir: ${INSTALL_DIR}"
echo "  service: ${SERVICE_NAME}.service"
echo "  bind mode: ${BIND_MODE}"
echo "  requested port: ${REQUESTED_PORT}"
echo "  selected port: ${PORT}"

if [[ "${PRESERVING_ENV}" == "1" && "${PORT}" != "${REQUESTED_PORT}" ]]; then
  echo "  note: keeping port ${PORT} from the existing .env"
elif [[ "${PORT}" != "${REQUESTED_PORT}" ]]; then
  echo "  note: ${REQUESTED_PORT} was busy, so the installer chose ${PORT}"
fi

mkdir -p "${INSTALL_DIR}"
source_dir="$(pwd -P)"
install_dir_resolved="$(cd "${INSTALL_DIR}" && pwd -P)"
case "${source_dir}/" in
  "${install_dir_resolved}/"*)
    if [[ "${source_dir}" != "${install_dir_resolved}" ]]; then
      echo "--install-dir cannot be a parent of the source repository" >&2
      exit 1
    fi
    ;;
esac
if [[ "${source_dir}" != "${install_dir_resolved}" ]]; then
  rsync_excludes=(
    --exclude '.env'
    --exclude 'state/'
    --exclude '.git/'
    --exclude '.agents/'
    --exclude '.codex/'
    --exclude '__pycache__/'
    --exclude '*.pyc'
  )
  case "${WAKE_STATE_FILE}" in
    "${INSTALL_DIR}/"*)
      wake_state_relative="${WAKE_STATE_FILE#"${INSTALL_DIR}/"}"
      rsync_excludes+=(--exclude "/${wake_state_relative}")
      ;;
  esac
  rsync -a --delete "${rsync_excludes[@]}" ./ "${INSTALL_DIR}/"
else
  echo "Source is already ${INSTALL_DIR}; skipping file synchronization."
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod +x "${INSTALL_DIR}/install-on-pi.sh"
service_group="$(id -gn "${SERVICE_USER}")"
if [[ ! -e "${WAKE_STATE_DIR}" ]]; then
  install -d -o "${SERVICE_USER}" -g "${service_group}" -m 0750 \
    "${WAKE_STATE_DIR}"
elif [[ ! -d "${WAKE_STATE_DIR}" ]]; then
  echo "Wake-on-LAN state parent is not a directory: ${WAKE_STATE_DIR}" >&2
  exit 1
fi
if ! runuser -u "${SERVICE_USER}" -- test -w "${WAKE_STATE_DIR}"; then
  echo "User ${SERVICE_USER} cannot write to ${WAKE_STATE_DIR}." >&2
  echo "Adjust its owner/group permissions and run the installer again." >&2
  exit 1
fi

if [[ -n "${MEDIA_BACKUP_ROOT}" ]]; then
  if [[ ! -e "${MEDIA_BACKUP_ROOT}" ]]; then
    install -d -o "${SERVICE_USER}" -g "${service_group}" -m 0750 "${MEDIA_BACKUP_ROOT}"
  elif [[ ! -d "${MEDIA_BACKUP_ROOT}" ]]; then
    echo "Media backup root is not a directory: ${MEDIA_BACKUP_ROOT}" >&2
    exit 1
  fi

  if ! runuser -u "${SERVICE_USER}" -- test -w "${MEDIA_BACKUP_ROOT}"; then
    echo "User ${SERVICE_USER} cannot write to ${MEDIA_BACKUP_ROOT}." >&2
    echo "Adjust its owner/group permissions and run the installer again." >&2
    exit 1
  fi

  media_state_dir="$(dirname "${MEDIA_BACKUP_ROOT}")/.pistats-media-state"
  install -d -o "${SERVICE_USER}" -g "${service_group}" -m 0750 \
    "${media_state_dir}"
fi

if [[ ! -f "${ENV_PATH}" || "${FORCE_ENV}" == "1" ]]; then
  generated_token="0"
  if [[ -z "${TOKEN}" ]]; then
    TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
    generated_token="1"
  fi

  cat >"${ENV_PATH}" <<EOF
PISTATS_TOKEN=${TOKEN}
PISTATS_BIND_MODE=${BIND_MODE}
PISTATS_PORT=${PORT}
PISTATS_BACKUP_LABEL=${BACKUP_LABEL}
PISTATS_MEDIA_BACKUP_MAX_BYTES=${MEDIA_MAX_BYTES}
PISTATS_MEDIA_BACKUP_READ_TIMEOUT_SECONDS=${MEDIA_READ_TIMEOUT}
PISTATS_WAKE_BROADCAST=${WAKE_BROADCAST}
PISTATS_WAKE_PORT=${WAKE_PORT}
PISTATS_WAKE_STATE_FILE=${WAKE_STATE_FILE}
EOF

  if [[ -n "${HOST}" ]]; then
    cat >>"${ENV_PATH}" <<EOF
PISTATS_HOST=${HOST}
EOF
  fi

  if [[ -n "${WAKE_MAC}" ]]; then
    cat >>"${ENV_PATH}" <<EOF
PISTATS_WAKE_MAC=${WAKE_MAC}
EOF
  fi

  if [[ -n "${MEDIA_BACKUP_ROOT}" ]]; then
    cat >>"${ENV_PATH}" <<EOF
PISTATS_MEDIA_BACKUP_ROOT=${MEDIA_BACKUP_ROOT}
EOF
  fi

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
  echo "Configuration flags do not overwrite an existing .env; edit it directly or use --force-env."
  if [[ -n "${MEDIA_BACKUP_ROOT}" ]]; then
    echo "Note: --media-backup-root does not modify an existing .env."
    echo "Set PISTATS_MEDIA_BACKUP_ROOT=${MEDIA_BACKUP_ROOT} in ${ENV_PATH}, then restart the service."
  fi
fi

if ! grep -q '^PISTATS_WAKE_STATE_FILE=' "${ENV_PATH}"; then
  echo "PISTATS_WAKE_STATE_FILE=${WAKE_STATE_FILE}" >>"${ENV_PATH}"
  echo "Added persistent Wake-on-LAN state path to ${ENV_PATH}"
fi
chown "${SERVICE_USER}:${SERVICE_USER}" "${ENV_PATH}"
chmod 600 "${ENV_PATH}"

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
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "${SERVICE_PATH}"
systemctl daemon-reload

if [[ "${START_SERVICE}" == "1" ]]; then
  systemctl enable "${SERVICE_NAME}.service"
  systemctl restart "${SERVICE_NAME}.service"
  systemctl status "${SERVICE_NAME}.service" --no-pager || true
else
  echo "Installed service unit at ${SERVICE_PATH} without starting it."
fi

echo
echo "PiStats install complete."
echo "Config file: ${ENV_PATH}"
echo "Service: ${SERVICE_NAME}.service"
echo "Port: ${PORT}"
if [[ "${PRESERVING_ENV}" == "1" ]]; then
  echo "Token: keeping the existing value in ${ENV_PATH}"
elif [[ "${generated_token:-0}" == "1" ]]; then
  echo "Token: generated and written to ${ENV_PATH}"
else
  echo "Token: using the value provided to --token"
fi
echo "Show token:"
echo "  grep '^PISTATS_TOKEN=' ${ENV_PATH}"

if [[ "${BIND_MODE}" == "tailscale" ]]; then
  echo
  echo "Next checks:"
  echo "  tailscale ip -4"
  echo "  curl -H \"Authorization: Bearer <token>\" http://<tailscale-ip>:${PORT}/api/stats"
  echo "  curl -X POST -H \"X-Wake-Token: <token>\" http://<tailscale-ip>:${PORT}/api/wakeonlan/wake"
fi
