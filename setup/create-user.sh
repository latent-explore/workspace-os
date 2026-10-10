#!/usr/bin/env bash
# Create the wos sandbox user. Safe to re-run. Run with sudo.
#   sudo setup/create-user.sh [--dry-run]
set -euo pipefail

DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
run() { if [ "$DRY" -eq 1 ]; then echo "  would run: $*"; else "$@"; fi; }
[ "$DRY" -eq 1 ] || [ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

if getent passwd wos >/dev/null; then
  echo "User wos already exists: skipped."
else
  run useradd -m -s /bin/bash wos
  run passwd -l wos          # no password login; set one yourself with: sudo passwd wos
fi
run chmod 700 /home/wos

if [ -n "${SUDO_USER:-}" ] && [ "$DRY" -eq 0 ]; then
  if runuser -u wos -- test -r "/home/$SUDO_USER" 2>/dev/null; then
    echo "WARNING: wos can read /home/$SUDO_USER. Fix with: chmod 700 /home/$SUDO_USER" >&2
  else
    echo "Good: wos cannot read /home/$SUDO_USER."
  fi
  runuser -u wos -- sudo -n true 2>/dev/null && echo "WARNING: wos has sudo rights. Remove them." >&2 || echo "Good: wos has no sudo."
fi

cat <<'NEXT'

Next, by hand (these need your login and tokens, so the setup agent cannot do them):
  sudo -iu wos
  curl -fsSL https://claude.ai/install.sh | bash ; exec bash -l ; claude     # log in, then /exit
  gh auth login --with-token        # paste a fine-grained token limited to the repos wos needs
  gh auth setup-git ; git config --global user.name "<you>" ; git config --global user.email "<you@example.com>"
  git clone https://github.com/<you>/workspace-os.git
Then re-run: sudo setup/status.sh
NEXT
