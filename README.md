# workspace-os

A personal system where AI agents work on your workspaces 24/7 on one always-on Linux machine. You steer from chat; agents do the work.

## Get started

You need a Linux machine with systemd, an admin account (with sudo), Claude Code and `gh` for that account.

1. As the admin account: clone this repo.
2. Open Claude Code in it: `claude`
3. Say: "set up workspace-os"

The setup agent follows `base/plugin/skills/workspace-os/SKILL.md`. It runs `setup/status.sh`, tells you the next step, and shows the sudo and `wos`-user commands for you to run. Start over any time with `sudo setup/uninstall.sh`.

## Layout

| Folder | What |
|---|---|
| `docs/` | Plan, design, notes. Start with `docs/plan.md`. |
| `base/template/` | Seed files copied into each new workspace. |
| `base/plugin/` | Generic skills shared by every workspace. |
| `system/` | Gateway, runner, scheduler, policy, systemd units. |
| `setup/` | Host check, `wos` user, services. |

## Related repos (yours, private)

- `ws-base`: your personal skills and agents.
- `ws-index`: your registry, schedules and reports.

Status: planning. See `docs/plan.md`.
