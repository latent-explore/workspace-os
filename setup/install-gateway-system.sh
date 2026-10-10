#!/usr/bin/env bash
# Install the gateway as a system service that runs as the `wos` user. Safe to re-run.
# Run with sudo, from YOUR copy of the repo (not wos's copy, which wos could edit):
#   sudo setup/install-gateway-system.sh [/home/wos]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WOS_HOME="${1:-/home/wos}"
APP="$WOS_HOME/workspace-os"
ENV_FILE="$WOS_HOME/.config/workspace-os/env"

id wos >/dev/null 2>&1 || { echo "User wos does not exist." >&2; exit 1; }
[ -d "$APP/system" ] || { echo "Clone workspace-os into $APP first." >&2; exit 1; }
[ -x "$WOS_HOME/.local/bin/claude" ] || { echo "Claude Code is not installed for wos." >&2; exit 1; }

# Build the venv as wos (never as root).
runuser -u wos -- bash -c "
  [ -x '$APP/system/.venv/bin/python' ] || python3 -m venv '$APP/system/.venv'
  '$APP/system/.venv/bin/pip' install -q -e '$APP/system'
"

# SELinux (Fedora): systemd may only start programs labelled bin_t, not files in a home folder.
# Label just the venv's launcher; the service then runs in the unconfined_service_t domain.
if command -v getenforce >/dev/null && [ "$(getenforce)" != "Disabled" ]; then
  PATTERN="$APP/system/\\.venv/bin(/.*)?"
  semanage fcontext -a -t bin_t "$PATTERN" 2>/dev/null || semanage fcontext -m -t bin_t "$PATTERN"
  restorecon -R "$APP/system/.venv/bin"
fi

# Secrets: create an empty file if missing. I never copy or read secrets.
install -d -m 700 -o wos -g wos "$WOS_HOME/.config/workspace-os"
[ -e "$ENV_FILE" ] || install -m 600 -o wos -g wos /dev/null "$ENV_FILE"
if ! grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$ENV_FILE"; then
  echo "Fill in $ENV_FILE (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID), then re-run this script." >&2
  exit 1
fi

# Stop the old user-level service first: two copies would fight over Telegram.
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "wos" ]; then
  uid="$(id -u "$SUDO_USER")"
  runuser -u "$SUDO_USER" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
    systemctl --user disable --now wos-gateway.service 2>/dev/null || true
fi

sed "s|@HOME@|$WOS_HOME|g" "$REPO/setup/systemd/wos-gateway.system.service.in" \
  > /etc/systemd/system/wos-gateway.service
systemctl daemon-reload
systemctl enable wos-gateway.service
systemctl restart wos-gateway.service  # re-run applies changes
sleep 3
systemctl --no-pager status wos-gateway.service | head -6
