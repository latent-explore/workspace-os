#!/usr/bin/env bash
# Install the gateway as a systemd user service. Safe to re-run. Needs no sudo.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -x "$REPO/system/.venv/bin/python" ]; then
  python3 -m venv "$REPO/system/.venv"
  "$REPO/system/.venv/bin/pip" install -q -e "$REPO/system"
fi

# systemd services get a minimal PATH, so write the absolute path of claude into the unit.
CLAUDE="$(command -v claude || true)"
[ -n "$CLAUDE" ] || { echo "claude not found in PATH. Install Claude Code first." >&2; exit 1; }

mkdir -p "$HOME/.config/systemd/user"
sed -e "s|@REPO@|$REPO|g" -e "s|@CLAUDE@|$CLAUDE|g" "$REPO/setup/systemd/wos-gateway.service.in" \
  > "$HOME/.config/systemd/user/wos-gateway.service"
systemctl --user daemon-reload
systemctl --user enable wos-gateway.service
systemctl --user restart wos-gateway.service  # re-run applies changes
systemctl --user --no-pager status wos-gateway.service | head -5

echo
echo "Still needs sudo (run once, yourself):"
echo "  sudo loginctl enable-linger $USER                      # start at boot, before login"
echo "  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target"
