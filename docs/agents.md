# Agents

## The idea in four lines
- An agent is a **Claude agent file** (`.md`): a prompt, a tool list and a model. Nothing more.
- Agents do not know about Telegram. A **surface** (Telegram, terminal, later Slack or Discord) only picks an agent and carries text and approvals.
- Agents come from **plugins** loaded with `--plugin-dir`, so a `git pull` updates all of them.
- Permissions stay as they are: **cards**. A tool list says what an agent *can* use; the permission check says what it *may* use right now.

## The roster

| Agent | Scope | Tools | Who starts it | Status |
|---|---|---|---|---|
| **admin** | The whole wos userspace | All tools | Only me: terminal, Reception, any future surface | Plan A and B |
| **chat** | No workspace. General Q&A. | Read, Glob, Grep | My private chat | Plan A |
| **task** | One task in one workspace | Coder set (read, edit, shell) | The dispatcher | Plan A |
| **lead** | One workspace (its persona) | Read, edit, shell per grants | Messages in that workspace's group | Plan D |
| **reporter** | All workspaces (ambient) | Read | Scheduler: daily brief, weekly review | Plan F |
| **housekeeper** | All workspaces (ambient) | Read, shell for cleanup | Scheduler: nightly | Plan F |
| **improver** | All workspaces (ambient) | Read; opens PRs | Scheduler: daily, after weeks of logs | Plan F |
| **reviewer** | A PR or diff | Read | After a task opens a PR | Plan F |
| **workspace-specific** (e.g. `email-triage`) | One workspace only | Chosen per agent | Its workspace | Made when a workspace matures |

Where files live:
- Generic agents: `base/plugin/agents/*.md` (this repo, the `wos` plugin).
- My personal agents: a second plugin in `ws-base`.
- Workspace-specific agents: `<workspace>/.claude/agents/*.md`.
- A workspace-specific agent can copy or specialise a generic one.

## The admin agent
The one high-privilege agent. It is me, by proxy.

- **Reach:** anything inside the `wos` user's home. Create, edit and delete any file, run any command, manage workspaces, groups, repos, MCP servers, permissions. No tool restrictions: every tool is available. It cannot go beyond `wos` (no root), because the Linux user is the wall.
- **Who:** only me. Telegram: the allowlist and the Reception group. Terminal: a login as `wos`. A new surface must pass the same check.
- **Default mode: manual.** Only reads are pre-allowed. Everything else raises a card (Telegram) or the normal prompt (terminal), and I say yes or no.
- **"Always allow"** works for admin too, saved as admin-scope rules. I can remove them.
- **Different from other agents:** things that are a hard deny for others (editing permissions, touching the secrets folder, raw `gh`) become an **ask** for admin. I approve each one. No silent changes to the rules.
- **Later:** a "trusted" mode I can switch on for a limited time, so routine work does not need a tap each time.
- It uses the kernel helper `bin/wos` for system work, because that is safe and checked. It is not limited to it.

## Plan
Small steps, in this order. Each ends with a test.

**A. Agents as files**
- Make `base/plugin` a real plugin (`.claude-plugin/plugin.json`). Add `agents/chat.md`, `admin.md`, `task.md`.
- `runner.py` uses `--plugin-dir base/plugin --agent wos:<name>`. The prompt constants leave the code.
- Check: the task agent still codes well; approvals still work; same results as today.

**B. Admin privilege**
- `admin.md` lists every tool. Permission check gets an admin scope: reads allowed, everything else asks; the old hard denies become asks; "Always allow" saves admin-scope rules.
- Terminal entry: `bin/wos-admin` starts `claude --agent wos:admin` as `wos`. In a terminal, Claude's own prompts do the asking.
- Reception keeps using admin, with cards.
- Check: a file edit asks, I allow it, it works; a denied one stays denied.

**C. Surfaces are adapters**
- Write down the contract: a surface can `notify(text)`, `ask(question, options)`, and deliver a card answer. Telegram is adapter one. No agent code mentions it.
- No new code now; Discord and Slack come later.

**D. A lead per workspace**
- The template gets `.claude/agents/lead.md`. Linked groups run `--agent lead`.

**E. New permissions**
- `plugin`: install skills and agents at workspace-os scope. `limits`: change the run cap, up to a hard maximum.

**F. Ambient agents**
- Reporter first (daily brief from run logs and tasks), then housekeeper, then improver and reviewer once there are weeks of logs. All run from the scheduler. Anything they change is a PR.

**G. Group creation (decision needed)**
- A bot cannot create a Telegram group. Doing it needs a user-account (MTProto) client logged in as me. Until I decide, I create the group and the admin agent binds it.

**H. Later**
- Admin handles schedules, health checks, tasks and housekeeping.

## Vision (not built, kept here)
Three kinds of agents:
1. **Workspace-specific.** Made when a workspace matures and has needs of its own. Usable only there.
2. **Workspace-os agents.** Generic, and the most common. Plugins and skills are installed at this scope. They can be general versions of the workspace-specific ones.
3. **Ambient agents.** Self-improvement, housekeeping and reporting. They run on their own at workspace-os level.

Workflows every agent knows, written down once as skills:
- How tasks are managed (GitHub issues or another platform).
- How autonomous tasks run: how instructions arrive, how updates go out, how long tasks are managed and finished.
- How the scheduler works.
- How the messaging surfaces work (Telegram, Slack, Discord).
