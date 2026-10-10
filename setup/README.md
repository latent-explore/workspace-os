# setup

| Script | Job |
|---|---|
| `install-gateway.sh` | Builds a venv, installs the gateway as a systemd user service, starts it. Safe to re-run. |

Logs: `journalctl --user -u wos-gateway -f`
Status: `systemctl --user status wos-gateway`

Needs sudo, run once by hand:
- `sudo loginctl enable-linger $USER` (start at boot without logging in)
- `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target` (never sleep)

The dedicated `wos` Linux user comes before task mode (step 3), when agents get write access.
