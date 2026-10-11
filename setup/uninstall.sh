#!/usr/bin/env bash
# Undo what setup installed. Run with sudo, from your own checkout (not wos's).
#
#   sudo setup/uninstall.sh                  stop and remove the gateway service and its SELinux rule. Keeps wos and all data.
#   sudo setup/uninstall.sh --remove-user    ALSO delete the wos user and /home/wos: workspaces, registry,
#                                            tasks, secrets and the Claude login are gone for good.
#   sudo setup/uninstall.sh --unmask-sleep   ALSO let the machine sleep again.
#   add --dry-run to only print the plan, --yes to skip the question.
set -euo pipefail

DRY=0; YES=0; REMOVE_USER=0; UNMASK=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;; --yes) YES=1 ;; --remove-user) REMOVE_USER=1 ;; --unmask-sleep) UNMASK=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[ "$DRY" -eq 1 ] || [ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

WOS_HOME="${WOS_HOME:-/home/wos}"
UNIT=/etc/systemd/system/wos-gateway.service
PATTERN="$WOS_HOME/workspace-os/system/\\.venv/bin(/.*)?"
OLD_USER_UNIT=""
[ -n "${SUDO_USER:-}" ] && OLD_USER_UNIT="$(getent passwd "$SUDO_USER" | cut -d: -f6)/.config/systemd/user/wos-gateway.service"

echo "Plan:"
echo "  - stop and disable wos-gateway, remove $UNIT"
echo "  - remove the SELinux label rule for the venv launcher"
[ -n "$OLD_USER_UNIT" ] && [ -e "$OLD_USER_UNIT" ] && echo "  - remove the old user-level service file $OLD_USER_UNIT"
[ "$REMOVE_USER" -eq 1 ] && echo "  - DELETE user wos and $WOS_HOME (workspaces, registry, tasks, secrets, Claude login)"
[ "$UNMASK" -eq 1 ] && echo "  - unmask sleep, suspend and hibernate"
echo "  Not touched: your repo checkout, your own Claude and GitHub logins, GitHub repos."

if [ "$DRY" -eq 1 ]; then echo "(dry run: nothing changed)"; exit 0; fi
if [ "$YES" -eq 0 ]; then
  read -r -p "Proceed? [y/N] " answer
  [ "$answer" = y ] || { echo "Cancelled."; exit 1; }
fi
if [ "$REMOVE_USER" -eq 1 ] && [ "$YES" -eq 0 ]; then
  read -r -p "This deletes $WOS_HOME for good. Type the username (wos) to confirm: " answer
  [ "$answer" = wos ] || { echo "Cancelled."; exit 1; }
fi

systemctl disable --now wos-gateway.service 2>/dev/null || true
rm -f "$UNIT"
systemctl daemon-reload
echo "Removed the gateway service."

if command -v semanage >/dev/null; then
  semanage fcontext -d "$PATTERN" 2>/dev/null && echo "Removed the SELinux rule." || echo "No SELinux rule to remove."
fi

[ -n "$OLD_USER_UNIT" ] && rm -f "$OLD_USER_UNIT" && true

if [ "$REMOVE_USER" -eq 1 ]; then
  if getent passwd wos >/dev/null; then
    loginctl disable-linger wos 2>/dev/null || true
    loginctl terminate-user wos 2>/dev/null || true
    pkill -u wos 2>/dev/null || true
    sleep 1
    userdel -r wos && echo "Deleted user wos and $WOS_HOME."
  else
    echo "User wos does not exist: skipped."
  fi
fi

if [ "$UNMASK" -eq 1 ]; then
  systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
  echo "Sleep is allowed again."
fi
echo "Done. Check with: setup/status.sh"
