# workspace-os

A personal system where AI agents work on your workspaces 24/7 on one always-on Linux machine. You steer from chat; agents do the work.

## Get started

1. Clone this repo.
2. Open Claude Code in it: `claude`
3. Say: "set up workspace-os"

The setup agent follows `CLAUDE.md`.

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
