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

## Agents, plugins and limits

Agents are files in plugins (see `docs/agents.md`). The runner starts one with `claude --agent <name> --plugin-dir ...`.

| Where | Who answers |
|---|---|
| My private chat and the Reception group | `wos:admin`: all tools, manual mode, in the wos home |
| A linked workspace group, and task runs | the workspace's default agent, `wos:workspace`, unless the owner picked another with `/agent` |
| A scheduled prompt | `wos:workspace` with no workspace (so everything asks) |

Admin is the only agent for which "hard denies" (the secrets folder, permission and state files, raw `gh`, plugin and limit changes) become a card. Those cards offer **Allow once / Deny**, never "Always". Only the admin agent can create workspaces or link groups; ordinary agents are denied those `bin/wos` commands. Inside `bin/wos` the admin agent proves itself with a one-run token (`WOS_ADMIN`), so an env flag set by hand does nothing.

**Reception (admin group).** One Telegram group is the admin channel.
1. Create the group, add the bot, make it an admin (turn on Topics if you want them).
2. Send `/admin` in the group. It becomes Reception (can be set once).
3. In any other group, send one message so the bot sees it. In Reception, `/groups` lists every group it has seen.
4. Create a workspace by chatting in Reception: "create workspace invoicer with https://github.com/me/api and https://github.com/me/web".
5. Link a group: `/bind <group id> <workspace>` (or ask in chat).

**Commands (Reception only):** `/groups`, `/workspaces`, `/bind <group id> <workspace>`, `/unbind <workspace>`, `/perms`, `/grant`, `/revoke`, `/allow`, `/unallow`, `/agents [workspace]`, `/agent <workspace> [name|default]`, `/plugins`, `/limits [N]`.
**Anywhere:** `/new`, `/schedules`, `/stop <id>`, `/resume <id>`, `/id`, and the task commands.

**Agent per workspace.** `/agent <workspace> <name>` picks who answers in that workspace's group and runs its tasks. The name can be any plugin agent (`plugin:agent`) or an agent file in the workspace's own `.claude/agents/`. `default` goes back to `wos:workspace`. `/agents <workspace>` lists the choices.

**Plugins.** `bin/wos plugin install https://github.com/owner/repo` (or `add NAME PATH`, `remove NAME`, `list`) installs a plugin at workspace-os scope; its agents and skills become available to every run. The built-in `wos` plugin is always loaded. Installed plugins live in `~/plugins` and are listed in `state/plugins.yaml`.

**Limits.** `/limits N` or `bin/wos limits set max_runs N` changes how many runs go at once, with no restart, up to `WOS_HARD_MAX_RUNS` (default 4). The default is `WOS_MAX_RUNS` (1).

**Terminal.** Log in as `wos` and run `bin/wos-admin`: the same admin agent, with Claude's own prompts doing the asking.

`bin/wos` also works from a shell: `workspace add|list|agent`, `groups`, `bind`, `unbind`, `agents`, `plugin`, `limits`, `perms`, `gh`, `mcp`, `task`, `admin`.

Env keys: `WOS_WORKSPACES_DIR` (default `~/workspaces`), `WOS_REGISTRY`, `WOS_REPO_DIR`, `WOS_ADMIN_DIR` (default the wos home), `WOS_PLUGINS_DIR` (default `~/plugins`).

## Tasks (step 3b)

A task is a small JSON record (`state/tasks/<id>.json`). The agent decides how an episode ends; the system only remembers and guarantees nothing is lost.

From chat (any linked group; in Reception or a private chat, name the workspace first):
`/addtask title | details`, `/tasks`, `/task <id>`, `/reply <id> <text>` (answers a blocked task and wakes it), `/cancel <id>`, `/retry <id>`.

The dispatcher runs every 10 seconds: it picks the next `ready` task (up to `WOS_MAX_RUNS`), makes a worktree per repo in the workspace's `repos.yaml`, and starts an agent with the task skill (`base/plugin/skills/task/SKILL.md`).
The agent reports with `bin/wos task ...` (`comment`, `link`, `status`, `resume`, `notify`).

- Agent leaves without a state: the task is `blocked` and I get a message.
- Run fails or the machine restarts mid-run: the task is retried (3 attempts), then `blocked`.
- Sleeping tasks wake when their time has come and a free check passes (`--check "cmd"` exit 0, or `--when-job-done`). No tokens are spent checking.
- Long jobs are a future todo (see `docs/plan.md`). `bin/wos task job ...` and `gateway/jobs.py` exist but are experimental: they need a systemd user manager for `wos` (linger) that is not set up, and agents are not told about them.
- Agents never see the bot token: messages go through an outbox that the gateway delivers.

## Permissions and approvals

Permissions are per workspace, kept in `state/permissions.yaml` (outside every workspace, so agents cannot edit it).
Only the user changes them: `/perms`, `/grant <workspace> <capability>`, `/revoke ...` in Reception, or `bin/wos perms ...` in a terminal (refused inside an agent run).

| Capability | Lets the workspace's agents |
|---|---|
| `edit_files` | edit and create files inside the workspace folder (default for new workspaces) |
| `shell` | run shell commands; risky ones (sudo, rm -rf, git push, curl pipes...) still ask (default for new workspaces) |
| `web` | fetch pages and search the web |
| `github` | use GitHub through `bin/wos gh`, on the workspace's repos only |
| `create_repos` | create private repos named for the workspace (`ws-<name>`, `<name>-*`) |
| `mcp_manage` | add or remove the workspace's MCP servers |

How a run is checked: only reads are pre-allowed. Every other tool call goes to a permission check (an MCP tool Claude Code calls through `--permission-prompt-tool`). The check answers allow or deny at once from the workspace's grants, and only otherwise raises a card in the task's topic: **Allow once / Always allow for <workspace> / Deny**. "Always allow" saves a rule (or a capability) for that workspace, and is never offered for risky commands. The run waits up to `WOS_APPROVAL_WAIT` seconds (default 300), then the agent is told no answer came and stops.

Connectors (Gmail, Calendar...) from the logged-in Claude account are visible to every run, but each call still goes through the check. Tap "Always allow" on a card, or give a workspace a whole connector up front: `/allow email mcp__claude_ai_Gmail__*` (`/unallow` removes it). Set `WOS_STRICT_MCP=1` to hide everything except the workspace's own MCP servers.

Hard denies for ordinary agents, no card: reading the secrets folder, raw `gh` (use `bin/wos gh`), `bin/wos perms`, plugin and limit changes. For the admin agent each of these is a card.
A saved "always allow" shell rule matches by prefix and is never saved or applied for commands with `$(`, backticks, redirects (`>`, `<`) or open-ended tools (`find`, `sed`, `tar`, `awk`...).
Claude Code runs plain read-only shell commands (`ls`, `uname`...) without asking, so those never reach the check.
Set `WOS_APPROVALS=0` to fall back to the old fixed allow lists.

**This is a policy layer, not a wall.** The check for protected names (secrets folder, rule files, `WOS_ADMIN`) looks at the command text, so shell tricks such as globs (`run-t*`) or quoting can slip past it for an agent that has the `shell` capability. Treat `shell` as trusting that agent with the `wos` user. It also means an ordinary agent that writes a file mentioning those names (for example in a workspace that is the workspace-os repo itself) is refused. Agents run as the same Linux user, so an agent with `shell` could still write code that reads the GitHub token. The wall is the `wos` user and the systemd sandbox. Real per-workspace GitHub isolation needs one fine-grained token per workspace (todo in `docs/plan.md`).
While a card is waiting, that run holds a run slot (`WOS_MAX_RUNS`), so other chats queue behind it.
