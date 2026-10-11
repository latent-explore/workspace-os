---
name: workspace-os
description: Set up workspace-os on this machine (`setup`) or create and link a workspace (`add <name>`). Use when the user says "set up workspace-os", "add a workspace", or runs /workspace-os.
---

# workspace-os skill

Two jobs: `setup` (once per machine) and `add <name>` (once per workspace).

## Rules (both jobs)
- **Plan first.** Say what you will do, then ask before creating repos or changing system settings.
- **Safe to re-run.** Check each step and skip what is done. Never move, overwrite or delete existing files.
- **Never handle secrets.** Do not read or print the secrets file or tokens. The user pastes them in themselves.
- **Sudo is the user's.** You cannot type a sudo password. Show the exact command, ask the user to run it (`! sudo ...`), then re-check.
- **Short replies.** One step at a time. End with a short report: done, skipped, needs the user.

## setup
Two actors: the **admin account** (has sudo; this repo is cloned here and you run here) and **wos** (the sandbox user, no sudo, steps done in `sudo -iu wos`). Run this skill as the admin account, never as wos: the root scripts must come from a copy wos cannot edit.

1. Run `setup/status.sh`. It is a read-only checklist; every `FIX` line has a command. Do the **first** FIX only, then re-run. Ask the user to run `sudo setup/status.sh` for the checks inside `/home/wos`.
2. The usual order the FIXes come in:
   1. Admin-side tools (git, gh, python venv, claude, no sleep). Show the install or mask command.
   2. `sudo setup/create-user.sh` creates the `wos` user (locked, private home, no sudo).
   3. **As wos, by hand** (you cannot do these; show the commands, the user runs them in `sudo -iu wos`):
      1. install Claude Code and log in (`curl -fsSL https://claude.ai/install.sh | bash`, then `claude`);
      2. `gh auth login --with-token` with a **fine-grained token limited to the repos wos needs**, then `gh auth setup-git` and set the git name and email. The token comes before the clone because the repo is private;
      3. `git clone https://github.com/<owner>/workspace-os.git` into `~/workspace-os`. This is the live copy; it is pull-only.
   4. Telegram: the user creates a bot with @BotFather and puts `TELEGRAM_BOT_TOKEN` in `/home/wos/.config/workspace-os/env`. The user sends `/id` to the bot and adds `TELEGRAM_ALLOWED_USER_ID`. You never see the values. (The installer creates the file empty if it is missing; run it once, fill the file, run it again.)
   5. `sudo setup/install-gateway-system.sh` installs and starts the gateway. Run it from the admin copy.
   6. Reception: the user creates a Telegram group, makes the bot an admin, sends `/admin` in it.
3. Finish when `sudo setup/status.sh` prints "All good." Report what was done and skipped. Next: `add` the first workspace.

Starting over: `sudo setup/uninstall.sh` removes the service; `--remove-user` also deletes wos and all its data (the user must have the bot credentials saved elsewhere first).

## add <name>
Best run from the Reception group (no sudo) or as `wos` in `/home/wos/workspace-os`. `bin/wos` works on the state of the user it runs as, so running it as the human's own account would create a workspace the gateway never sees.

1. Ask, one at a time: the name (lowercase letters, digits, dashes), a one-line purpose, and the repos (plain `https://github.com/owner/name` URLs; a repo that is not on GitHub must be pushed there first).
2. Check `bin/wos workspace list`. Stop if the name exists.
3. Confirm the name and repos with the user. Then: `bin/wos workspace add NAME --repo URL --repo URL`.
4. Put the purpose in the workspace's `docs/ROADMAP.md`.
5. Link a Telegram group: the user creates a group, adds the bot as admin, sends one message. Then `bin/wos groups` shows its ID and `bin/wos bind CHAT_ID NAME` links it. (In Reception: `/groups`, `/bind`.)
6. Optional: a `ws-NAME` GitHub repo for the workspace's own files. The wos token cannot create repos, so the user creates it with their own account (`gh repo create <you>/ws-NAME --private`); then, as wos, in the workspace folder: `git remote add origin ...` and `git push -u origin main`.
7. Report: workspace path, repos cloned, group linked or not, what needs the user.

Existing work is not moved. A workspace lives in wos's home, cloned from GitHub; push any local-only work to GitHub first.
