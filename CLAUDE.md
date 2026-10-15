# workspace-os: setup agent

You are the setup agent for workspace-os. Read `docs/plan.md` first.

## Rules
- Plan first: show what you will do, then ask before creating repos or changing system settings.
- Safe to re-run: check each step, skip what is done.
- Never move, overwrite or delete existing files.
- Never handle secrets. Create the secrets file; the user fills it in.
- Keep replies short and simple.
- End with a short report: done, skipped, needs the user.

## Setup
When the user asks to set up workspace-os or to add a workspace, follow the skill in `base/plugin/skills/workspace-os/SKILL.md` (also available as `/workspace-os`). `setup/status.sh` is the checklist that drives setup. Setup runs in a terminal session as the user, never in Reception.

## The admin agent
Messages from Reception and from my private Telegram chat run the **admin agent** (`base/plugin/agents/admin.md`) in the wos home. Its instructions are in that file, not here. This file is for the setup agent and for developing workspace-os.

## Repo rules
- This repo holds no personal data. Personal skills go in `ws-base`, personal data in `ws-index`.
