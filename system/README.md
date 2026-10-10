# system

The gateway: Telegram in, Claude Code out. Step 1 supports chat mode only (read-only).

## Run it

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Create the secrets file (only you can read it):
   ```
   mkdir -p ~/.config/workspace-os && touch ~/.config/workspace-os/env && chmod 600 ~/.config/workspace-os/env
   ```
   Add this line: `TELEGRAM_BOT_TOKEN=<token>`
3. Start it: `cd system && python3 -m gateway`
4. Send `/id` to your bot. Add `TELEGRAM_ALLOWED_USER_ID=<number>` to the env file. Restart.
5. Message the bot. `/new` starts a fresh conversation.

Optional env keys: `WOS_MODEL`, `WOS_MAX_RUNS` (default 1), `WOS_CHAT_DIR` (default: a neutral `chat` folder in the state dir), `WOS_STATE_DIR`, `WOS_RUN_TIMEOUT`.

## Test

`cd system && python3 -m unittest discover -s tests -t .`

## Files

| File | Job |
|---|---|
| `gateway/telegram.py` | Channel: poll, send, split long text, allowlist |
| `gateway/runner.py` | Runner: `claude -p`, resume, standard result |
| `gateway/state.py` | Session map, Telegram offset, one JSON log per run |
| `gateway/main.py` | Wires it together, one run per conversation, concurrency cap |

State lives in `~/.local/state/workspace-os/` (sessions, run logs).

## Task mode by hand (step 3a)

Makes one worktree per repo on a task branch, runs the agent with write access, and shows what changed.
The agent can commit but not push (`git push` and `gh` are denied); the system pushes and opens PRs later.

```
cd system
.venv/bin/python -m gateway.task run --workspace ~/ws --repos api,web --task 7-foo --prompt "..."
.venv/bin/python -m gateway.task clean --workspace ~/ws --repos api,web --task 7-foo
```
`WOS_TASK_TIMEOUT` (default 1800s) sets the time limit for a task run.

## Reception (admin group)

One Telegram group is the admin channel. Messages there run in the workspace-os folder as an admin agent that can only use `bin/wos`.

1. Create the group, add the bot, make it an admin (turn on Topics if you want them).
2. Send `/admin` in the group. It becomes Reception (can be set once).
3. In any other group, send one message so the bot sees it. In Reception, `/groups` lists every group it has seen.
4. Create a workspace by chatting in Reception: "create workspace invoicer with https://github.com/me/api and https://github.com/me/web".
5. Link a group: `/bind <group id> <workspace>` (or ask in chat).

Commands (Reception only): `/groups`, `/workspaces`, `/bind <group id> <workspace>`, `/unbind <workspace>`.
Anywhere: `/new`, `/schedules`, `/stop <id>`, `/resume <id>`, `/id`.

A linked group runs in its workspace folder (read-only chat for now). An unlinked group runs nothing and says so.
`bin/wos` does the same things from a shell: `bin/wos workspace add|list`, `bin/wos groups`, `bin/wos bind`, `bin/wos unbind`.

Env keys: `WOS_WORKSPACES_DIR` (default `~/workspaces`), `WOS_REGISTRY` (default: `workspaces.yaml` in the state dir), `WOS_REPO_DIR`.
