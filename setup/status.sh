#!/usr/bin/env bash
# Read-only checklist for workspace-os. Safe to run any time.
# Run with sudo to also check what lives inside /home/wos.
# Exit code = number of problems found.
set -uo pipefail

WOS_HOME="${WOS_HOME:-/home/wos}"
STATE="$WOS_HOME/.local/state/workspace-os"
ENV_FILE="$WOS_HOME/.config/workspace-os/env"
problems=0
IS_ROOT=0; [ "$(id -u)" -eq 0 ] && IS_ROOT=1

ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FIX  %s\n         -> %s\n' "$1" "$2"; problems=$((problems + 1)); }
note() { printf '  note %s\n' "$1"; }
need_root() { printf '  ...  %s (run with sudo to check)\n' "$1"; }
as_wos() { runuser -u wos -- "$@"; }
# Host tools are checked as the person who ran the script: under sudo, root's PATH misses ~/.local/bin.
has() {
  if [ "$IS_ROOT" -eq 1 ] && [ -n "${SUDO_USER:-}" ]; then
    runuser -l "$SUDO_USER" -c "command -v $1" >/dev/null 2>&1
  else
    command -v "$1" >/dev/null 2>&1
  fi
}

echo "Host"
[ "$(uname -s)" = Linux ] && ok "Linux" || bad "Linux is required" "use a Linux machine"
has systemctl && ok "systemd" || bad "systemd is required" "use a systemd distro"
has git && ok "git" || bad "git missing" "install git"
has gh && ok "gh" || bad "gh missing" "install the GitHub CLI"
has claude && ok "claude (your user)" || bad "claude missing for your user" "install Claude Code"
python3 -c "import venv" 2>/dev/null && ok "python3 + venv" || bad "python3 venv missing" "install python3 (with venv)"
has tmux && ok "tmux" || note "tmux not installed (optional, useful for SSH sessions)"
has getenforce && note "SELinux: $(getenforce) (the installer labels the venv launcher)"
if [ "$(systemctl is-enabled sleep.target suspend.target hibernate.target 2>/dev/null | grep -vc '^masked$')" -eq 0 ]; then
  ok "sleep and suspend are disabled"
else
  bad "the machine may sleep" "sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target"
fi

echo "wos user (the sandbox)"
if getent passwd wos >/dev/null; then
  ok "user wos exists"
  perm="$(stat -c %a "$WOS_HOME" 2>/dev/null || echo ?)"
  [ "$perm" = 700 ] && ok "$WOS_HOME is private (700)" || bad "$WOS_HOME permissions are $perm" "sudo chmod 700 $WOS_HOME"
  if [ -n "${SUDO_USER:-}" ] && [ "$IS_ROOT" -eq 1 ]; then
    as_wos test -r "/home/$SUDO_USER" 2>/dev/null \
      && bad "wos can read /home/$SUDO_USER" "chmod 700 /home/$SUDO_USER" || ok "wos cannot read /home/$SUDO_USER"
  fi
else
  bad "user wos does not exist" "sudo setup/create-user.sh"
fi

if getent passwd wos >/dev/null; then
  echo "Inside wos"
  if [ "$IS_ROOT" -eq 1 ]; then
    [ -x "$WOS_HOME/.local/bin/claude" ] && ok "claude installed for wos" || bad "claude not installed for wos" "sudo -iu wos, then: curl -fsSL https://claude.ai/install.sh | bash"
    as_wos "$WOS_HOME/.local/bin/claude" --version >/dev/null 2>&1 && ok "claude runs as wos" || bad "claude does not run as wos" "sudo -iu wos, then run: claude (log in)"
    as_wos gh auth status >/dev/null 2>&1 && ok "gh logged in as wos" || bad "gh not logged in as wos" "sudo -iu wos, then: gh auth login --with-token ; gh auth setup-git"
    [ -d "$WOS_HOME/workspace-os/.git" ] && ok "workspace-os cloned for wos" || bad "workspace-os not cloned for wos" "sudo -iu wos, then: git clone https://github.com/<you>/workspace-os.git"
    if [ -f "$ENV_FILE" ]; then
      [ "$(stat -c %a "$ENV_FILE")" = 600 ] && ok "secrets file is private (600)" || bad "secrets file permissions" "sudo chmod 600 $ENV_FILE"
      for key in TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USER_ID; do
        grep -q "^$key=.\+" "$ENV_FILE" && ok "$key is set" || bad "$key is not set" "edit $ENV_FILE (see system/README.md)"
      done
    else
      bad "secrets file missing" "sudo setup/install-gateway-system.sh creates it; then fill it in"
    fi
  else
    need_root "claude, gh, clone and secrets inside $WOS_HOME"
  fi
fi

echo "Gateway"
if systemctl is-active --quiet wos-gateway; then
  ok "wos-gateway is running"
  [ "$(systemctl show wos-gateway -p User --value)" = wos ] && ok "running as wos" || bad "gateway is not running as wos" "sudo setup/install-gateway-system.sh"
else
  bad "wos-gateway is not running" "sudo setup/install-gateway-system.sh"
fi
systemctl is-enabled --quiet wos-gateway 2>/dev/null && ok "starts at boot" || bad "gateway will not start at boot" "sudo setup/install-gateway-system.sh"

echo "Telegram and workspaces"
if [ "$IS_ROOT" -eq 1 ]; then
  if [ -f "$STATE/chats.json" ] && python3 -c "import json,sys; sys.exit(0 if json.load(open('$STATE/chats.json')).get('admin') else 1)"; then
    ok "Reception group is set"
  else
    bad "Reception group is not set" "create a Telegram group, make the bot an admin, send /admin in it"
  fi
  count="$(grep -c '^  [a-z0-9-]*:$' "$STATE/workspaces.yaml" 2>/dev/null || true)"
  note "workspaces registered: ${count:-0}"
else
  need_root "Reception group and workspaces"
fi

echo
[ "$problems" -eq 0 ] && echo "All good." || echo "$problems thing(s) to fix. Start with the first FIX above."
exit "$problems"
