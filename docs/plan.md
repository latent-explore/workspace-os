# Workspace OS: Plan (v2)

Goal: help me scale myself. Add a workspace, and it runs with almost no attention from me.

This plan merges the design, the vision and the architecture notes. Where they differ, this file wins.

---

## 1. Big decisions

| Question | Decision |
|---|---|
| Build or adopt (OpenClaw, Hermes)? | Build a **small kernel I own**. Copy good ideas from OpenClaw and Hermes. Don't adopt either wholesale. |
| Why not OpenClaw? | Weak security record, and it would hold my mail and calendar. It's also a general assistant; my design is more opinionated (PR for every change, approval gates). |
| What is the AI agent? | A **worker**, not the boss. Claude Code first, Hermes or Codex later. The kernel owns state, rules, schedule and chat. |
| How do workers run? | **Headless CLI** for now: `claude -p --output-format json`, resumed with `--resume <session_id>`. The Agent SDK is a **future todo**. |
| Chat vs task? | Runner input has `mode: chat \| task`. **Chat** = free conversation in the workspace root, read-only, no deliverable. **Task** = work in a worktree, with a result. |
| Does the contract cost tokens? | No. The JSON wrapper (`result`, `session_id`, `usage`) is made by the CLI, not the model. In chat, the model's text is forwarded as is. |
| Where does chat live? | In **my kernel**, not inside any harness. Telegram first, Discord later. |
| Which GitHub account? | `latent-explore`. |
| Database? | **Plain files first** (one JSON per task, one log per run). Add SQLite only when I need queries. |
| tmux? | Dropped. Workers run headless. |
| Self-improvement? | A scheduled job that opens PRs. Built **later**, after weeks of logs. |

---

## 1b. Repos (3)

| What | Repo | Visibility | Contents |
|---|---|---|---|
| **Core product** | `workspace-os` | Public-ready | System code (gateway, runner, scheduler, policy), setup, workspace template, generic skill pack (`workspace-os` skill, GitHub task skill, scheduling skill), docs. |
| **My shared skills** | `ws-base` | Private | My personal skills, agent files, account-wide decisions. |
| **Workspace data** | `ws-index` | Private | Registry, instance settings, schedules, reports, global index. No code. |

- Anyone clones `workspace-os` and runs the setup agent. Setup asks where the base and index repos are, and creates blank ones if missing.
- Skills are **referenced** (two plugins per workspace: generic from `workspace-os`, personal from `ws-base`; mine win on clashes). The template is **copied once**.
- Secrets live outside all repos, in `~/.config/workspace-os/env`.
- Rule of thumb: a stranger would want it -> `workspace-os`. My taste or lesson -> `ws-base`. My data -> `ws-index`.
- `ws-system` from the first design is gone; its code is `workspace-os/system/`.
- Layout of `workspace-os`: `docs/`, `base/template/`, `base/plugin/`, `system/`, `setup/`, `README.md`, `CLAUDE.md` (setup agent entry).

---

## 2. The kernel: 4 interfaces

Each has a few operations. Swap what's behind it without touching the rest.

| Interface | Operations | Plain meaning |
|---|---|---|
| **Runner** | run, resume | A contractor. Gets a work order, a folder and allowed tools. Returns a standard result. |
| **Channel** | `notify(text)`, `ask(question, options)` | One front door to me. Telegram now, Discord later. |
| **Task store** | next_ready, claim, comment, set_status, link | The to-do list lives outside any agent's head. GitHub issues only for now. |
| **Policy** | `check(action)` → allow / ask / deny | One rulebook for risky actions: send, delete, spend, merge. |

**Three file conventions (not code):**
- Skills: `SKILL.md` folders.
- Memory: markdown in git. A tiny always-loaded file plus a searchable archive.
- Task record: one JSON per task (task ID, chat thread, worktree, session ID, status) plus its run log.

---

## 3. How it fits together

```
 Me (Telegram)
      |
 [ Gateway ]  one Python process, always on
   - Channel (Telegram, allowlist)
   - Run queue (one run per session, concurrency cap)
   - Scheduler (cron + heartbeat)
   - Policy (approval gates)
   - Task store (GitHub issues)
      |
 [ Worker ]  one per run, runs as the `wos` Linux user, inside the task's worktree
   Claude Code headless CLI  (later: SDK, Hermes, Codex)
```

**A Telegram message becomes a run:**
1. Check the allowlist, find the task for this topic.
2. No session yet → start a run, save the `session_id`. Session exists → resume it.
3. Worker finishes. Gateway shortens the result to 5 lines and sends it.

**Two modes**

| | Chat | Task |
|---|---|---|
| Purpose | Talk, plan, ask questions | Produce a deliverable (PR, results) |
| Folder | Workspace root | Task worktree (one per repo in the plan) |
| Permissions | Read-only | Read and write |
| Model's job | Reply naturally, short by instruction | Do the work, end with a one-line summary |
| Kernel's job | Forward the text, save the session ID | Parse status, files changed, tokens, PR link |
| Ends when | I stop talking | Deliverable is done |

Example planning chat: idea → questions and answers (each message is a `--resume`) → I tap **Approve** → kernel writes the GitHub issue → kernel starts a **task** run in a new worktree.

Chat replies are never re-summarized. The "reply short" rule is in the Lead's system prompt. Replies over 4096 characters are split or sent as a file by the Telegram adapter. Long chats cost more (each resume reloads history), so move the plan into the issue early.

Notes:
- The session ID lives on disk, so a crash loses nothing.
- Run from the task's worktree each time (resume may depend on the folder).
- Never put message text into a shell string.
- Mid-task questions: the worker ends with "needs input", the gateway asks me, then resumes with my answer.
- Permissions are set up front, since nobody can click "allow" mid-run. Policy hooks check risky actions.

---

## 4. Scheduler (core)

Part of the gateway. Two styles, copied from OpenClaw:
- **Cron:** exact time. "Daily brief at 07:00."
- **Heartbeat:** a cheap check every 30 min. Wakes an agent only if something needs doing.

A schedule costs **zero tokens** until it fires. Every entry has an owner and an end date. I can list or stop any entry from Telegram.

---

## 5. Workspaces

Same as the design: a folder plus a small `ws-<name>` repo. Registry says where each lives. Existing ones are registered in place. Each task gets its own worktree.

**First four:**

| Workspace | Kind | Starting mode |
|---|---|---|
| **AI Updates** | Scheduled job: daily and weekly paper summaries | Read and write notes only. Safe. |
| **Email** | Scheduled job: sort, summarize, delete junk | Read and draft only. Deletes need my OK at first. |
| **Calendar** | Scheduled job: plan days from emails and reminders | Suggest only, then write with approval. |
| **Research Assistant** | Real tasks: knowledge base, blogs, experiments. Each project can be its own workspace. | Uses GitHub issues and PRs. |

Email and Calendar are **scheduled jobs, not tasks**. Don't force them through GitHub issues.

**Cross-workspace links:** a workspace can write a note into another one's inbox folder. Example: AI Updates drops a paper into the Research Assistant; Email drops a deadline into Calendar. Add this only after two workspaces exist.

---

## 6. Safety

- Agents run as a **dedicated `wos` Linux user** with CPU/RAM limits. This is the real safety wall. Don't rely on each harness's own approvals, since headless runs usually skip them.
- **Policy layer** is mine. Agents may read, draft and edit in their workspace freely. These need me: send, delete, spend, merge, deploy, messages to other people.
- Trust grows one rule at a time. Example: "delete mail from sender X" becomes automatic after I approve it a few times.
- Secrets in one locked file outside every repo. Connectors get least access.
- One allowlist at the gateway, checked before any model sees a message.
- Every change, including self-improvements, is a PR.
- No marketplace skills. Only skills I wrote or reviewed.

---

## 7. Staying up 24/7 (Phase 4, simplified)

systemd only. Units:

| Unit | Job |
|---|---|
| `wos-gateway.service` | Runs the gateway. Restarts on crash. On startup, resumes unfinished tasks. |
| `wos-agents.slice` | CPU and RAM cap for all workers. |
| `wos.target` | One on/off switch. |

Also: disable sleep, create the `wos` user, give it its own `claude login` and `gh auth`.

Changed from before: the dispatcher and relay are now **one** gateway, so one service instead of two.

---

## 8. Open risks (check early)

| # | Risk | Check |
|---|---|---|
| 1 | **Billing.** On Pro, SDK and `claude -p` usage may draw from a separate monthly credit (reported from 2026-06-15). Third-party use of subscription login is restricted. | **TODO (later):** read Anthropic's Help Center page on the Agent SDK with a Claude plan. Postponed by choice; could change the plan. |
| 2 | **Pro limits.** Four daily workspaces on Pro will hit limits. | Start with one workspace, measure tokens per run, set a budget per workspace. |
| 3 | `--resume` may need the same folder. | **Tested 2026-10-10: works from a different folder too.** Same session ID, history kept. Sessions are stored per folder, so the copy is saved under the new folder as well. Still resume from the same folder to keep one log. |
| 4 | (Future) SDK feels weaker than CLI. | Only when the SDK is adopted: set the `claude_code` prompt preset, load `CLAUDE.md` and settings, run a parity test. |
| 5 | Prompt cache expires when idle, so resumed sessions cost more. | Measure. |
| 6 | Hermes persistence and one-shot behavior unverified. | **TODO (later):** test before Hermes becomes a second harness. |

---

## 9. Build order

**Step 0: Spike (done).** `--resume` works from the same and a different folder (risk 3). Cost of a tiny run: about $0.002 equivalent, mostly cached context. The billing check (risk 1) is postponed.

**Step 1: Kernel skeleton. DONE (2026-10-10).** Runs as a systemd user service under my own account; the `wos` user moves to just before Step 3.
- Create `workspace-os` (this repo), `ws-base` and `ws-index` on `latent-explore`.
- `wos` user, secrets file, systemd units.
- Telegram channel with allowlist: send a message, get a reply.
- *Done when:* I message the bot, a worker answers, and it survives `kill -9` and a reboot.

**Step 2: Scheduler. DONE (2026-10-10).** One thread in the gateway, state on disk, catch-up, `/schedules` `/stop` `/resume`, systemd watchdog (tested by freezing the process).
- Cron and heartbeat in the gateway.
- *Done when:* a test job sends me a Telegram message at a set time, with no tokens spent until it fires.

**Step 3: Task store + dispatcher.**
- GitHub issue -> worktree -> worker -> PR -> card in Telegram.
- Policy layer with approve/deny buttons.
- *Done when:* an issue turns into a reviewed PR with no help from me.

**Step 4: Setup skill.**
- `/workspace-os setup` and `/workspace-os add`. Safe to re-run, never overwrites.
- *Done when:* `add` creates a blank test workspace end to end (folder, repo, registry entry, Telegram topic).

**Setup is now complete. Workspaces come after this, onboarded with `add`.**

**Step 5: First workspace.**
- Onboard it with `/workspace-os add`. Pick it at that point (AI Updates is the safest, Email the most useful).
- *Done when:* its daily summary arrives on Telegram at 07:00 without me.

**Step 6: More workspaces.** Email, then Calendar, then Research Assistant. Read and draft only at first. Link them once two exist.

**Later (todo):**
- Billing check for headless `claude -p` on Pro.
- Hermes (or Codex) as a second harness, and the runner test suite.
- Agent SDK.

**Defer until I have weeks of logs and it hurts:** Improver, Housekeeper, global index, Grafana, Discord, showcase site, sharing, company instance.

Rule: build only what the next workspace needs.

---

## 10. What changed from the first design

- Gateway replaces separate dispatcher and relay.
- Scheduler moved into v1.
- Setup skill comes before any workspace; the first workspace is onboarded with it.
- SQLite/Postgres dropped for now; files first.
- tmux dropped.
- Added the 4-interface model and the Policy layer.
- Headless CLI instead of the SDK (SDK is a future todo).
- Added chat vs task modes, so free conversation costs no extra tokens.
- Added billing and Pro-limit risks as step 0.
- Slack replaced by Telegram; Discord is future.
- Cut from v1: Improver, Housekeeper, Grafana, index.

---

## 11. Open items

1. Billing check: postponed (todo).
2. Hermes as second harness: postponed (todo).
3. First workspace: chosen after setup is done.
