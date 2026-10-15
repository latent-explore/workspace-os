# Agents

## The idea in four lines
- An agent is a **Claude agent file** (`.md`): a prompt, a tool list and a model.
- Agents do not know about Telegram. A **surface** (Telegram, terminal, later Slack or Discord) only picks an agent and carries text and approvals.
- Agents come from **plugins** loaded with `--plugin-dir`. Installing a plugin makes its agents and skills available.
- Permissions stay as cards. A tool list says what an agent *can* use; the permission check says what it *may* use right now.

## Agents we build now
Two files in the `wos` plugin (`base/plugin/agents/`):

| Agent | Scope | Tools | Used by |
|---|---|---|---|
| **admin** | The whole `wos` userspace | All tools | Only me: terminal, Reception, my private chat, any future surface |
| **workspace** | One workspace (the default agent) | Read, edit, shell per the workspace's grants | Chat in a workspace group, and task runs |

Everything else comes from plugins I install. Any plugin's agent can be used.

### admin
The one high-privilege agent. It is me, by proxy.
- **Reach:** anything in the `wos` home: create, edit, delete any file, run any command, manage workspaces, groups, repos, MCP servers, plugins, permissions. Every tool is available. It cannot go beyond `wos` (no root): the Linux user is the wall.
- **Who:** only me. Telegram allowlist, a login as `wos`, and any future surface must pass the same check.
- **Manual mode by default.** Only reads are pre-allowed. Everything else raises a card (Telegram) or the normal prompt (terminal). "Always allow" saves an admin-scope rule that I can remove.
- **Hard denies for other agents become an ask for admin:** editing permissions, the secrets folder, raw `gh`. I approve each one. Nothing changes silently.
- **Later:** a "trusted" mode, on for a limited time, so routine work needs fewer taps.
- `bin/wos-admin` is only a launcher for the terminal. It starts the same admin agent with the right flags. No agent can start it.
- Only the admin agent creates workspaces and links groups. Ordinary agents are denied those commands.
- The secrets folder, the rule files (`permissions.yaml`, `plugins.yaml`, `limits.yaml`) and the state folder always ask for admin, offer no "always", and are denied to everyone else.

### workspace (the default agent)
What answers when I chat in a workspace group. Its prompt says: you work in this workspace; its repos are in `repos.yaml`; reply in at most 5 short lines, one question at a time; read `docs/ROADMAP.md` for goals; write what you learn to `docs/LOG.md`; put long answers in a file. The workspace's own `CLAUDE.md` adds the specifics.
- **Task runs** use the same agent plus the `task` skill (how to report, sleep, and end an episode).
- **Careful:** an agent file inside a workspace's own `.claude/agents/` is editable by that workspace's agents. Only select such an agent after you have read it.
- **The default can be changed per workspace.** Admin sets it: `/agent <workspace> <name>`. The name can be a plugin agent, or an agent file in the workspace's own `.claude/agents/`. Stored in the registry as `agent:`.

## Plan
Small steps, in this order. Each ends with a test. **A to D are built** (E waits for details).

**A. Agents as files**
- Make `base/plugin` a real plugin (`.claude-plugin/plugin.json`) with `agents/admin.md` and `agents/workspace.md`. The `task` and `workspace-os` skills stay.
- `runner.py` uses `--plugin-dir base/plugin --agent wos:<name>`. The prompt constants leave the code.
- Private chat goes to admin. Chat in a workspace group and task runs go to the workspace's default agent.
- Check: same results as today; the approval flow still works.

**B. Admin privilege**
- `admin.md` lists every tool. The permission check gets an admin scope: reads allowed, everything else asks; secrets and permission edits ask (not deny); "Always allow" saves admin-scope rules.
- `bin/wos-admin` launcher for the terminal.
- Check: an edit asks, I allow it, it works; a denied one stays denied.

**C. Default agent per workspace**
- Registry field `agent:`. Commands `/agent <workspace> <name>` and `bin/wos workspace agent`. The gateway resolves the name before each run.
- Check: switching a workspace's agent changes who answers.

**D. Plugins and limits**
- Plugins: admin installs and removes plugins at workspace-os scope (`bin/wos plugin ...`, `/plugins`). The list lives in `state/plugins.yaml`; the runner passes one `--plugin-dir` per entry. Their agents and skills become available.
- Limits: admin changes the run cap (`bin/wos limits set max_runs N`, `/limits N`), up to a hard maximum.
- These are owner-level commands, not workspace capabilities: ordinary agents are denied, the admin agent gets a card (allow once / deny, never "always"). Inside the CLI the admin agent proves itself with a per-run token, so setting an env flag by hand does nothing.
- Check: install a plugin, see its agent appear, use it in a workspace.

**E. Create groups from the admin agent (MTProto)**
A bot cannot create a Telegram group. A user-account client can. Goal: "create a group for workspace X" in Reception, and the group appears, is set up and is linked, with no manual steps.
1. I get an `api_id` and `api_hash` at my.telegram.org.
2. One-time login (`bin/wos telegram login`, interactive: phone, code, 2FA). The client library is Telethon. The session file goes in the secrets folder, mode 600, and is off limits to every agent.
3. `bin/wos group create NAME` does this: creates a supergroup with topics on; adds the bot as admin with Manage Topics; reads the new chat ID; binds it to the workspace. Each use asks for a card.
4. Safety: only the gateway-side helper touches the session, never the agent's code. Cap on groups per day. Telegram may restrict an account that automates heavily.
5. **Decision:** use a dedicated Telegram account to own the groups (safer, needs another number), or my personal one (simpler, but the session file then grants full access to my account). Recommended: dedicated.
- Check: on a throwaway group, create it, see the bot as admin, see it linked.

## Later (todo)
- Admin handles schedules and health: create, read, update and delete schedules; health checks. Built when those features are fully developed.

## Vision (not planned yet)
Three kinds of agents:
1. **Workspace-specific.** Made when a workspace matures and has needs of its own. Usable only there. They can specialise a generic agent.
2. **Workspace-os agents.** Generic, and the most common. Plugins and skills are installed at this scope.
3. **Ambient agents.** Self-improvement, housekeeping, reporting and review. They run on their own at workspace-os level, from the scheduler, and anything they change is a PR. Examples: reporter (daily brief, weekly review), housekeeper (nightly cleanup), improver (reads run logs, proposes better skills and prompts), reviewer (checks a PR before I see it).

Workflows every agent knows, written down once as skills:
- How tasks are managed (GitHub issues or another platform).
- How autonomous tasks run: how instructions arrive, how updates go out, how long tasks are managed and finished.
- How the scheduler works.
- How the messaging surfaces work (Telegram, Slack, Discord).
