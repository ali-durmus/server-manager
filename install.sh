#!/usr/bin/env bash
set -euo pipefail

APP_NAME="server-manager"
SRC_FILE="server_manager.py"
INSTALL_DIR="/usr/local/bin"
TARGET_PATH="${INSTALL_DIR}/${APP_NAME}"

echo "[*] Installing ${APP_NAME}..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] python3 not found. Please install Python 3 first."
  exit 1
fi

if [[ ! -f "${SRC_FILE}" ]]; then
  echo "[!] ${SRC_FILE} not found in current directory."
  exit 1
fi

chmod +x "${SRC_FILE}"

if [[ ! -d "${INSTALL_DIR}" ]]; then
  echo "[!] ${INSTALL_DIR} does not exist."
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "[*] Root privileges required to write to ${INSTALL_DIR}"
  sudo install -m 755 "${SRC_FILE}" "${TARGET_PATH}"
else
  install -m 755 "${SRC_FILE}" "${TARGET_PATH}"
fi

echo "[+] Installed to ${TARGET_PATH}"
echo "[+] Run it with: ${APP_NAME}"
