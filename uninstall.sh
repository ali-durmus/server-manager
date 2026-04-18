#!/usr/bin/env bash
set -euo pipefail

APP_NAME="server-manager"
TARGET_PATH="/usr/local/bin/${APP_NAME}"

echo "[*] Removing ${TARGET_PATH} ..."

if [[ -e "${TARGET_PATH}" ]]; then
  if [[ "${EUID}" -ne 0 ]]; then
    sudo rm -f "${TARGET_PATH}"
  else
    rm -f "${TARGET_PATH}"
  fi
  echo "[+] Uninstalled."
else
  echo "[!] ${TARGET_PATH} not found."
fi
