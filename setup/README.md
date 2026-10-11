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

## Running as the `wos` user (sandbox)

| Script | Job |
|---|---|
| `install-gateway-system.sh` | Installs the gateway as a system service running as `wos`, with systemd sandboxing. Run with sudo **from your own copy of the repo**, since `wos` could edit its own copy. |

Logs: `sudo journalctl -u wos-gateway -f`
Status: `systemctl status wos-gateway`
Restart: `sudo systemctl restart wos-gateway`

## Setup scripts

| Script | Job | Run |
|---|---|---|
| `status.sh` | Read-only checklist; `FIX` lines say what to do next | any time; `sudo` for the checks inside `/home/wos` |
| `create-user.sh` | Create the `wos` user (locked, private, no sudo) | `sudo`, `--dry-run` available |
| `install-gateway-system.sh` | Install and start the gateway as `wos` | `sudo`, from the admin copy |
| `uninstall.sh` | Remove the service; `--remove-user` also deletes wos and its data | `sudo`, `--dry-run` available |

Run the root scripts from the admin copy of the repo, never from `/home/wos`: wos could edit its own copy.
