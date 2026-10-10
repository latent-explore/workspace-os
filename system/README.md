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
