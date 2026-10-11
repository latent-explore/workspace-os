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

# Kill wos's processes one by one, never this script or anything above it.
kill_wos_processes() {
  local keep=" " pid=$$ p
  while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
    keep+="$pid "
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  done
  for p in $(pgrep -u wos 2>/dev/null); do
    case "$keep" in *" $p "*) continue ;; esac
    kill -KILL "$p" 2>/dev/null || true
  done
}

# Stopping wos's processes would also kill this script if it was started from inside a wos session.
if [ "$REMOVE_USER" -eq 1 ]; then
  pid=$$
  while [ "$pid" -gt 1 ]; do
    if [ "$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')" = wos ]; then
      echo "This shell runs inside a wos session (process $pid). Open a fresh login as your admin account (a new SSH connection), then run this again." >&2
      exit 1
    fi
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [ -n "$pid" ] || break
  done
fi

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
    echo "Stopping wos processes..."
    loginctl disable-linger wos 2>/dev/null || true
    loginctl terminate-user wos 2>/dev/null || true
    kill_wos_processes     # an open wos shell ignores polite signals, so this is a hard kill
    sleep 1
    echo "Deleting the user..."
    userdel -r wos || { echo "userdel failed. Still running as wos:" >&2; ps -u wos -o pid,tty,cmd >&2; exit 1; }
    if getent passwd wos >/dev/null || [ -e "$WOS_HOME" ]; then
      echo "wos or $WOS_HOME is still there. Check: getent passwd wos ; ls -ld $WOS_HOME" >&2; exit 1
    fi
    echo "Deleted user wos and $WOS_HOME."
  else
    echo "User wos does not exist: skipped."
  fi
fi

if [ "$UNMASK" -eq 1 ]; then
  systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
  echo "Sleep is allowed again."
fi
echo "Done. Check with: setup/status.sh"
