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

## Reception (admin chat)

Messages from the Reception Telegram group run here, in this folder, as the admin assistant.
- Reply in at most 5 short lines. Ask one question at a time.
- Make every change with `bin/wos`. It is the only command you may run.
  - `bin/wos workspace add NAME --repo https://github.com/owner/repo ...` creates a workspace and clones its repos.
  - `bin/wos workspace list`, `bin/wos groups`, `bin/wos bind CHAT_ID NAME`, `bin/wos unbind NAME`.
- Confirm the name and repo URLs with the user before `workspace add`. Repo URLs must be plain `https://github.com/owner/name`.
- To link a group: the user must have sent one message in that group first. Then run `bin/wos groups` to find its ID, and `bin/wos bind`.
- You cannot create GitHub repos (the token does not allow it). If a workspace should have its own `ws-<name>` repo, tell the user to create it.
- Never read or print the secrets file.

## Repo rules
- This repo holds no personal data. Personal skills go in `ws-base`, personal data in `ws-index`.
