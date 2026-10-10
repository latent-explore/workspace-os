# Workspace OS: Architecture Decisions and Research Notes

Date: 2026-10-10
Scope: everything discussed after the Telegram vs Discord debate (build vs adopt, OpenClaw and alternatives, harness-agnostic design, abstractions, connectors, SDK-based gateway).

Confidence labels used below:
- **Verified**: stated in official docs or multiple sources.
- **Reported**: from a single blog, issue or third-party summary.
- **My judgment**: reasoning, not tested.

---

## 1. Context

**Goal.** A personal workspace management system that runs 24/7 on an always-on desktop, is fault tolerant and observable, and can be driven from any device (phone, tablet, laptop). Each new workspace (Email, Calendar, AI Updates, Research Assistant, later business/finance/health) should run with minimal ongoing attention.

**Key requirements from the vision doc**
- Always on, services restart after crash or reboot, unfinished tasks resume.
- GitHub issues are tasks; one git worktree per task; multi-repo tasks give one PR each.
- Scheduler (cron) for summaries, planning and check-ins, with no token cost until a job fires.
- Rate-limit handling, concurrency cap.
- Chat control: Telegram first, Discord later, one topic/thread per task, short replies (5 lines max), Approve / Change / Discuss buttons, allowlist.
- Safety: every change is a PR, approval gates for merges/deploys/spending/deletions/messages to others, separate Linux user with CPU/RAM limits, secrets outside repos, least-access connectors.
- Learning: run logs, an improver that proposes skill/prompt changes as PRs, housekeeper, global index.
- Reports: daily brief, weekly review, token budget tracking.

**Questions explored**
1. Adopt something like OpenClaw, or build from scratch?
2. How to avoid being locked into one harness (Claude Code vs Codex vs Hermes)?
3. Which abstractions are worth having without over-engineering?
4. Where should the chat connector live, and does it affect model performance?
5. What if the gateway is written by hand on top of each harness's SDK?

---

## 2. Summary of conclusions

1. **Do not write the whole stack from scratch, and do not adopt OpenClaw wholesale without hardening.** The common middle path is a thin kernel you own, with harnesses as replaceable workers.
2. **The harness is a worker.** Workspace OS owns state, policy, scheduling and chat. Each harness (Claude Code, Codex, Hermes) sits behind a small runner interface.
3. **Four real interfaces:** Runner, Channel, Task store, Policy.
4. **Three file conventions (not code abstractions):** skills as `SKILL.md` folders, memory as markdown in git, one task record per task (plus run log).
5. **Self-improvement is not an abstraction.** It is a scheduled job that reads run records and opens PRs.
6. **The chat connector belongs at the Workspace OS level**, not inside any harness.
7. **A shared connector does not hurt Claude's performance.** What matters is harness plus model, not the transport.
8. **Using each harness's own SDK gives near-CLI performance**, because the SDK runs the same loop. The real work is configuration parity, billing/auth, and a few SDK gaps.
9. **OpenClaw can serve as the connector layer** (gateway, sessions, queue) and run Claude Code and others through ACP. This is a legitimate shortcut with a security trade-off.
10. **Current direction (user):** write a minimal gateway, with one SDK-based worker per provider, starting with Claude Code and Hermes.

---

## 3. Build vs adopt

### 3.1 OpenClaw

**What it is (Verified).** Open-source (MIT) personal AI agent runtime. Started as Clawdbot, was briefly Moltbot, and settled on OpenClaw in January 2026. Its founder announced in February 2026 that he was joining OpenAI, and that the project would move to a foundation.

**Design (Verified).** Gateway-centric. One long-running Node.js process (the Gateway) owns channels, sessions, routing, cron, heartbeat and tool execution. Memory is plain files. Skills are markdown folders. The agent core is decoupled from the LLM.

**Security record (Verified, multiple sources)**
- Securelist counts around 530 vulnerabilities across OpenClaw and its underlying technologies in under two years. Most involve sensitive-data storage and excessive privileges.
- Koi Security audited all 2,857 skills on ClawHub and found 341 malicious ones. Later reports say the count grew to 824+ as the marketplace grew past 10,700 skills.
- Cisco found nine vulnerabilities (two critical) in the top-ranked community skill. A broader audit of 31,000 agent skills found 26% had at least one vulnerability.
- One roundup says 543 CVEs name OpenClaw as affected, including a one-click remote code execution (CVE-2026-25253, CVSS 8.8).
- The Dutch data protection authority advised against deploying experimental agents like OpenClaw on systems with sensitive or regulated data.
- Skills run with no effective sandbox, and until early February 2026 no skills had any security check.

**Why not adopt wholesale (My judgment)**
1. The security history matters because the agent would hold mail, calendar and shell access.
2. It is a general personal assistant. Your design is opinionated (PR for every change, GitHub-issue queue, worktree per task, approval gates, separate Linux user).
3. You would still build the registry, onboarding, cross-workspace links, improver and reports yourself.

**If used anyway:** run under a dedicated Linux user or container, keep the localhost binding and channel allowlist, avoid marketplace skills, stay on current releases, run its security audit command.

**Ideas worth copying:** memory as plain files, heartbeat plus cron, skills as folders, a gateway that owns sessions and delivery.

### 3.2 Alternatives

| Option | Notes | Caveats |
|---|---|---|
| **Hermes Agent** (Nous Research) | MIT, Python, launched Feb 2026. One gateway process for Telegram, Discord, Slack, WhatsApp, Signal. Built-in cron, MCP, learning loop (agent-written skills), any model provider, six sandbox backends reported, `hermes claw migrate` for OpenClaw users. | Far fewer releases than OpenClaw (11 vs 137 per one review). A clean agent-specific CVE record partly reflects less exposure. One review notes a ChromaDB dependency and more setup. |
| **Coding harnesses** (Claude Code, Codex CLI, Gemini CLI) | Run headless as workers. | See section 4. |
| **Smaller "claw" variants** (ZeroClaw, NanoClaw, Nanobot, TrustClaw), **NemoClaw** (NVIDIA security stack on OpenClaw) | Named in roundups. | **Not vetted.** Treat as leads only. |

No source credibly named a "most reliable" option. Everything here is under a year old, so reliability should come from your own supervisor, run records and resumable tasks.

### 3.3 Claude Code Channels

- Released as a research preview on 2026-03-20. Lets Telegram or Discord messages flow into a running Claude Code session over MCP, with two-way replies.
- Requires Claude Code 2.1.80 or later and a claude.ai login (API keys not supported). Teams/Enterprise orgs must enable it. Reported setup uses a plugin plus a pairing code, and Bun.
- Only the paired account's messages are accepted.
- It is Claude-specific, so it works against the goal of not being tied to one harness.
- **Unclear (sources disagree):** whether permission prompts can be answered remotely. One guide says Claude Code pauses for approval, another says the permission system does not work remotely. **Test this before relying on it.**
- Community project claude-hermes wraps Claude Code as a daemon with Telegram/Discord bridges, cron and a thread-per-session model. Worth reading, not adopting blindly.

---

## 4. Staying harness-agnostic

**Principle.** Workspace OS owns state, policy and scheduling. The harness only does the thinking and editing for one task at a time.

**You own:** workspace registry, GitHub-issue task queue, scheduler, chat adapter, approval gates, run logs, memory as markdown in git.

**Runner interface (concept).** Takes a task (issue), a working directory (the task's worktree) and a permission level. Starts the harness headless inside the sandboxed Linux user. Returns one standard result (status, final message, files changed, tokens, session ID). Resumes or recovers using the session ID or run record. Writes the run log in one common format. Each harness gets a small adapter.

**Headless support (Verified).** Claude Code, Codex (`codex exec`, JSON events, resume) and Gemini CLI all have non-interactive modes.

**Safety lesson.** Headless runs typically require bypassing permission prompts. A reported Codex issue says MCP tool calls in `codex exec` are cancelled unless the dangerous bypass flag is used. Therefore **your own sandbox (separate Linux user, limits) and your own Policy layer must be the safety boundary**, not each harness's approval system.

**What detaching costs**
- Channels is Claude-specific, so you write the Telegram adapter yourself (small).
- Billing and auth differ per harness (see section 9.4).
- Hermes's learning loop edits its own skills, which conflicts with "every self-improvement is a PR I approve". Constrain it if used.

**The abstraction has limits.** It only covers what harnesses share (run, return result, resume). Harness-specific extras fall outside it. Plan to run the same workspace through a second harness early to see where it leaks.

---

## 5. The abstractions

Rule of thumb (My judgment): add an interface only where you could realistically swap what is behind it **and** can list its operations in five lines. Otherwise use a file convention. Both OpenClaw and Hermes get most of their power from plain markdown files, not interfaces.

### 5.1 Four interfaces

| Interface | Operations | Intuition | Where seen |
|---|---|---|---|
| **Runner** | run, resume | Replaceable contractor given a work order, a workshop and allowed tools | Partly: OpenClaw and Hermes decouple the model; swapping the *harness* is your extension. OpenClaw's ACP does run external harnesses (section 8). |
| **Channel** | `notify(text)`, `ask(question, options)` | One front door for everything that reaches you | Both. OpenClaw sends heartbeat and cron output through the same channel adapters. Hermes runs one gateway across platforms. |
| **Task store** | next_ready, claim, comment, set_status, link | The to-do list lives outside any agent's head, so crashes and harness swaps lose nothing | Neither. They are conversation-centric. This is your own addition. |
| **Policy** | `check(action)` returns allow / ask / deny | A guard that checks one rulebook before risky actions (send, delete, spend, merge) | Both, but built into the agent (OpenClaw tool policies; Hermes defence-in-depth). You lift it into your own layer. |

### 5.2 Three file conventions (not code)
- **Skills:** `SKILL.md` folders. OpenClaw lets workspace skills override global ones. Hermes follows the agentskills.io standard.
- **Memory:** markdown in git. Copy Hermes's discipline: a tiny always-loaded file (capped around 2,200 characters) plus a searchable archive.
- **Task record:** one JSON per task tying task ID, chat thread, worktree, harness session ID and status, plus its run log.

### 5.3 Task management: narrow, not wide
- The interface should be **narrow** (five operations), not wide. Narrow is what lets many trackers fit. A wide interface only fits the tracker it was copied from.
- Implement **GitHub only** for now. A Jira or Sheets adapter would have to flatten GitHub-specific parts (issue to worktree to PR, multi-repo).
- Email and calendar workspaces are mostly scheduled jobs, not tasks. Do not force them through the task store.

### 5.4 Self-improvement: a job, not an abstraction
- OpenClaw has none. Its memory is files, not learning.
- Hermes builds it into the agent loop: the agent writes and updates skills through a tool, and a curator loop archives unused ones. This ties it to Hermes's runtime and applies changes directly, which conflicts with the PR-approval rule. Its own guide cautions it works best when you actively correct and prompt the agent.
- Portable version: the improver is a scheduled workspace job. It reads run records and opens PRs against skills and prompts. Its only dependencies are the run-record format and the skills folder.
- Build it after you have weeks of run logs. The design effort goes into the **run-record format**.

### 5.5 What to skip
- **Scheduler:** use systemd timers or cron. Copy OpenClaw's two styles: *heartbeat* (periodic check-in, default every 30 minutes) and *cron* (exact time). Cron can inject an event into the main session or run in an isolated one.
- **Sandbox backends:** a separate Linux user is enough. Abstract only if you move to containers.
- **Connectors:** MCP is already the abstraction.
- **Model provider:** the harness handles it.

---

## 6. Where the chat connector lives

**Decision: Workspace OS level.**

- OpenClaw's Gateway handles routing, sessions, tool execution and persistence, with the agent behind it, so channels are not an agent property even there.
- Hermes bundles its gateway with the agent because Hermes is both kernel and harness.
- Claude Code Channels is the one true harness-level version, and it requires a claude.ai login.

**Reasons to keep it in your layer**
1. The conversation outlives any single run.
2. Not all traffic comes from a harness (daily brief, crash notices, approvals). You should not start a harness and spend tokens just to message yourself.
3. Approvals belong to your Policy layer.
4. One allowlist, enforced once, before any model sees the message (also reduces prompt-injection surface).
5. Several harnesses can run side by side and share one thread per task.

**Cooperation through events.** The runner reports progress or "needs input" upward, and Workspace OS turns that into `notify` / `ask`. The harness never knows Telegram exists.

**Both harnesses use the same connector.** Do not enable each harness's native chat feature, or you get duplicate bots, two allowlists and split threads.

### 6.1 Native connectors: do they work the same way?
No (documented behavior; internals beyond that not confirmed).

| | Who owns the chat | Continuity |
|---|---|---|
| **Your headless pattern** | Workspace OS | Stored session ID; short-lived process per message |
| **Claude Code Channels** | Claude Code | One long-lived interactive session; messages injected as events |
| **Hermes gateway** | Hermes (one process) | Hermes's own in-process session store; scheduler and memory in the same process |

Native connectors are fine for a first-weekend prototype or casual chats. They cannot carry scheduler or policy traffic.

### 6.2 Does a shared connector hurt Claude's performance? (My judgment, not measured)
No. The model only sees prompt text and tool results; it cannot tell where a message came from. What Claude Code is tuned for is its **inner loop** (system prompt, tools, context handling), and headless mode keeps that loop.

What can actually matter:
- **Prompt content.** Give a clear task (issue text plus context), not raw chat banter.
- **Do not make the agent format for chat.** Apply the "5 lines max" rule in Workspace OS (summarize, or ask for a short summary field). Chunking and platform formatting belong in the channel adapter.
- **Mid-task questions** need a protocol: the agent ends with "needs input", Workspace OS asks you, then resumes with your answer.
- **Resume cost** may differ from a live session. Measure it.
- **Harness plus model is the variable.** Claude inside Hermes behaves differently from Claude inside Claude Code. Run the same task through both to compare.

---

## 7. How a Telegram message becomes a run (headless pattern)

```
# first message in a Telegram topic (run inside that task's worktree)
claude -p --output-format json            # prompt via stdin

# every follow-up in the same topic
claude -p --output-format json --resume <session_id>
```

Flow per message:
1. Adapter checks the allowlist and looks up the task record for the topic.
2. No session ID yet: start a new run and save the returned `session_id`.
3. Session ID exists: resume with the follow-up text.
4. Parse the JSON result (`result`, `session_id`, `usage`), shorten to the 5-line limit, send it back.

Notes
- Verified: `-p` is the non-interactive flag (not `--headless`); JSON output includes `session_id`; `--resume` continues by ID; `--output-format stream-json` streams events.
- Continuity lives in the stored session ID, not a running process, which suits fault tolerance.
- **Never interpolate message text into a shell string.** Use stdin or an argument list from code.
- **One run at a time per session.** Queue a second message that arrives mid-run (My judgment).
- **Run from the task's worktree each time.** Claude Code likely stores sessions per project directory, so a resume from elsewhere may not find it. Verify on your machine.
- Set permissions up front (`--permission-mode`, `--allowedTools`) because nobody can answer prompts mid-run.
- Streaming JSON input exists for feeding several turns into one process. It could replace per-message processes later but is more to manage.

**Hermes as a headless worker (Verified).** Hermes has a one-shot non-interactive CLI prompt (can select toolsets, preload skills, isolate in a worktree) and an OpenAI-compatible API server with a Runs API for one-shot executions and a Jobs API for cron-style work. The gateway is not required.

---

## 8. How OpenClaw does connectors, and Claude Code via ACP

**Connectors (Verified).** Hub-and-spoke. One Gateway (Node, WebSocket server, localhost by default). Each platform has a small adapter: Telegram via grammY, Discord via discord.js, WhatsApp via Baileys. The Gateway maps each message to a session, queues it (serial per session by default), runs the agent, and streams the reply back through the same adapter. Several channels can run at once and OpenClaw routes per chat. Telegram is the quickest setup (a bot token).

**Claude Code with OpenClaw (Verified).** OpenClaw can launch external coding harnesses through ACP (Agent Client Protocol) and manage them as sessions.

```
Telegram -> OpenClaw Gateway -> ACP -> Claude Code
```

- Spawn from chat: `/acp spawn claude --bind here` (ties the Claude Code session to the current conversation). Spawn creates or resumes a session, records it in OpenClaw's session store, and tracks it as a background task. `/acp cancel` stops the current turn; `/acp close` ends the session.
- Documented chain: OpenClaw ACP session control, ACPX plugin, Claude ACP adapter, Claude Code. Requires Claude Code to be signed in on the host.
- ACP also covers Codex, Gemini CLI, Cursor, OpenCode and others, which fits the goal of not being tied to one harness.
- Reported: the Claude Code session holds its own conversation history, so OpenClaw does not re-feed it.
- An alternative direction exists: `openclaw mcp serve` lets Claude Code or Codex connect as MCP clients to existing OpenClaw channel conversations.
- OpenClaw policy checks an allowed-agents setting before dispatching to a harness.

**What this would replace:** chat adapters, session mapping, queue, scheduling. **What you would still build:** GitHub-issue dispatcher, worktrees, approval rules, run records, improver.

**One-week trial suggestion:** OpenClaw plus Claude Code through ACP, with one read-only workspace (email summary). Tasks, skills and memory stay as markdown in git, so moving to your own kernel later costs little.

---

## 9. Writing your own minimal gateway on top of agent SDKs

**Proposed design (user).** A server app per provider (Hermes, Claude Code, Codex...). Each uses the provider's agent SDK, with sessions maintained in one Python process per agent run. If the SDK manages history and caching, offload to it; if not, keep the conversation in memory.

**Verdict (My judgment, backed by docs).** Matching CLI performance is mostly automatic **if you embed each harness's own loop and do not rewrite it.**

### 9.1 What each SDK gives you
- **Claude Agent SDK (Verified):** runs on the same runtime as Claude Code (same built-in tools, permission system, automatic prompt caching, event stream). `ClaudeSDKClient` for multi-turn; `resume=session_id` for disk-backed resume. The Python SDK drives the Claude Code CLI underneath. Hooks are Python callbacks invoked at points in the agent loop (for example inspecting a Bash command before it runs).
- **Codex (Verified):** the TypeScript SDK wraps the CLI; the Python SDK controls the local Codex app-server over JSON-RPC. Threads start, resume and stream events. Sessions persist on disk. Approval control is available programmatically. One doc version calls the Python SDK experimental, a newer one stable. Check.
- **Hermes (Verified):** import `AIAgent`; use `chat()` or `run_conversation()`. Same environment variables as the CLI. `enabled_toolsets` can lock the agent down.

For Claude and Codex, history lives in the harness subprocess and on disk, not in your Python process.

### 9.2 Where parity breaks
1. **Claude SDK defaults differ from the CLI (Verified).** Minimal system prompt by default (request the `claude_code` preset). `CLAUDE.md`, settings and slash commands are not loaded unless `setting_sources` is set. This is the usual cause of "the SDK feels dumber than the CLI". The fix is two config lines.
2. **Do not hand-roll the loop.** Rebuilding system prompt, tool schemas, compaction and cache handling is the genuinely hard part and where performance would be lost. All three harnesses have an embeddable loop.
3. **Protect prompt caching (general knowledge).** Caching depends on a stable prefix, so only append to a session. Do not rewrite earlier context or change the tool list mid-session. Caches expire when idle, so the first message after a long gap misses the cache, as in the CLI. Cache lifetimes change, so check current docs.
4. **SDK coverage varies (Reported).** One third-party summary says the Codex TypeScript SDK has limited observability and no compaction access, and that the app-server protocol gives full control. Hermes persistence across restarts is unverified.
5. **Billing and auth (see 9.4).**

### 9.3 Bonus: more control than the CLI
Hooks let your Policy layer inspect risky actions in-process. The event stream lets you relay progress to Telegram. Codex has programmatic approval control.

### 9.4 Auth and billing for Claude (Reported, verify with Anthropic)
- Anthropic's docs say developers building products or services on the Agent SDK should use API-key authentication. Subscription OAuth in third-party tools is not permitted for building products.
- Anthropic staff said personal and local development use of the Agent SDK is explicitly fine.
- A GitHub issue quoting the Help Center says that from **2026-06-15**, Agent SDK and `claude -p` usage draws a separate monthly Agent SDK credit instead of interactive subscription limits. This also corroborates a blog claim about `-p` usage.
- April 2026 reports describe Anthropic blocking third-party harnesses from using subscription billing.
- **Read the current Help Center article before relying on any of this.** Policies have changed repeatedly.
- Hermes and Codex have their own auth and billing models (not researched).

### 9.5 Effort and how to check
- Rough guess (not sourced): a day or two per adapter; Hermes needs extra time to confirm persistence and one-shot behavior.
- **Parity test:** run 5 to 10 representative tasks through the CLI and through your adapter. Compare success, turn count and token usage from each result. Expect near-identical. Gaps point to configuration (prompt preset, setting sources, permission mode, model).

### 9.6 Suggested adjustments
- Keep **one gateway process** (channels, run queue, policy, task store). Run each task's agent as a **worker process** under the sandbox user.
- **Persist sessions by ID on disk**, not in memory, so a crash can resume.
- Start with Claude via the SDK, then Hermes.

---

## 10. Queue terminology

- **Run queue:** a waiting line in front of the harnesses. It holds a second message sent to a session that is mid-run (one run at a time per session), and holds runs beyond your concurrency cap or paused at a token limit. In code: a per-session lock plus a counter.
- **Task queue / task store:** your list of work (GitHub issues). It answers "what next?" A task spans many messages and runs.
- Connection: the task store says what is next, the dispatcher turns it into a run, and the run queue decides when that run may start.

---

## 11. Options compared

| Option | You build | You inherit | Main risk |
|---|---|---|---|
| **A. OpenClaw + ACP** | Dispatcher, worktrees, approvals, run records, improver | Channels, sessions, queue, cron, heartbeat, multi-harness via ACP | Security record; Anthropic auth/billing questions for subscription use |
| **B. Own minimal gateway + SDK workers** *(current direction)* | Gateway, channel adapters, run queue, runner adapters, everything in A | Same-loop performance from each SDK; hooks for policy | Config parity; SDK gaps; per-adapter maintenance |
| **C. Own gateway + CLI headless (`claude -p`, `codex exec`, Hermes one-shot)** | Same as B, with subprocess adapters | Simplest adapters | Less control than SDKs; weaker approvals when headless |
| **D. Hermes as kernel and harness** | Fewer pieces | Gateway, cron, memory, skills, any model | Hermes becomes the thing you are tied to; learning loop conflicts with PR rule; younger project |

**Current direction:** Option B, starting with Claude Code and Hermes. Option A remains a reasonable one-week spike if time-to-value matters more than control.

---

## 12. Things to verify before committing

1. Whether Claude Code Channels can handle approvals remotely, and whether it supports multiple parallel sessions.
2. Hermes: one-shot and API behavior in practice, and how history persists across process restarts.
3. Anthropic's current terms and billing for `claude -p` and the Agent SDK (Help Center article), especially for an always-on personal setup.
4. Whether `--resume` requires the same working directory (per-project session storage).
5. Prompt-cache lifetime and its effect on resumed sessions.
6. Codex Python SDK status (experimental vs stable).
7. How ACP billing works for Claude Code under OpenClaw (a single blog claims the ACP route passes because Claude Code itself makes the call).
8. The unvetted OpenClaw alternatives (ZeroClaw, NanoClaw, Nanobot, TrustClaw, NemoClaw).

---

## 13. Suggested build order

1. **Weekend 1, kernel:** supervisor (systemd), channel adapter with allowlist, separate Linux user, secrets file outside repos, crash and reboot recovery test.
2. **Week 1, first workspace:** Email summary or AI Updates (read and draft only, low risk).
3. **Then:** GitHub-issue dispatcher with worktrees, then Calendar. Link workspaces only after two exist.
4. **Early:** add a second harness (Hermes or Codex) and run the same workspace through it. Run the parity test.
5. **Defer:** improver, dashboard, sharing, until you have weeks of run logs.

Rule: build only what the next workspace needs.

---

## 14. Sources

**OpenClaw: overview and news**
- [OpenAI bets big on personal AI agents with OpenClaw (The Deep View)](https://www.thedeepview.com/articles/openai-bets-big-on-personal-ai-agents-with-openclaw)
- [Why OpenClaw (Clawdbot) Is Trending in 2026 (Powerdrill)](https://powerdrill.ai/ar/blog/why-openclaw-is-trending-and-what-it-means-for-ai-tools)
- [Official OpenClaw Breakdown (Vellum)](https://www.vellum.ai/md/blog/official-openclaw-breakdown)

**OpenClaw: security**
- [Here are the OpenClaw security risks you should know about (TechRadar)](https://www.techradar.com/pro/here-are-the-openclaw-security-risks-you-should-know-about)
- [OpenClaw security (Securelist)](https://securelist.com/openclaw-security/120484/)
- [OpenClaw is a Security Nightmare (Barrack AI)](https://blog.barrack.ai/openclaw-security-vulnerabilities-2026/)
- [OpenClaw: Hunting Season is Open (Immersive Labs)](https://www.immersivelabs.com/resources/c7-blog/openclaw-hunting-season-is-open)
- [OpenClaw Security Guide (Bitdoze)](https://www.bitdoze.com/openclaw-security-guide/)
- [OpenClaw security overview (clawdocs)](https://clawdocs.org/security/overview)

**OpenClaw: architecture and connectors**
- [Deep dive into the OpenClaw Gateway architecture](https://practiceoverflow.substack.com/p/deep-dive-into-the-openclaw-gateway)
- [OpenClaw Architecture Part 1: Control Plane, Sessions, and the Event Loop](https://theagentstack.substack.com/p/openclaw-architecture-part-1-control?open=false)
- [OpenClaw architecture (Kanerika)](https://kanerika.com/blogs/openclaw-architecture/)
- [OpenClaw Architecture Overview (clawdocs)](https://clawdocs.org/architecture/overview)
- [OpenClaw architecture deep dive (heyuan110)](https://www.heyuan110.com/zh/posts/ai/2026-02-14-openclaw-architecture-deep-dive/)
- [OpenClaw Agent Runner: Request Lifecycle Explained](https://dev.to/chwu1946/openclaw-agent-runner-request-lifecycle-explained-3cj1)
- [OpenClaw chat channels docs](https://docs.openclaw.ai/channels/index)
- [OpenClaw ACP agents docs](https://docs.openclaw.ai/tools/acp-agents/)
- [OpenClaw ACP agents docs (mirror)](https://documentation.openclaw.ai/tools/acp-agents)
- [ACPX runtime plugin (Steve Kinney)](https://stevekinney.com/courses/openclaw/acpx-runtime-plugin/llms.txt)
- [Running Claude Code in OpenClaw via ACP (blog)](https://cookbook.solomonneas.dev/ai-stack/acp-claude-code/)

**Alternatives: Hermes Agent**
- [Hermes Agent docs (APIYI)](https://docs.apiyi.com/en/scenarios/agent/hermes-agent.md)
- [Hermes Agent official docs](https://hermes-agent.nousresearch.com/docs)
- [Hermes Agent (Cloud Native Landscape)](https://landscape.jimmysong.io/projects/hermes-agent/)
- [Hermes Agent (Tencent Cloud Techpedia)](https://tencentcloud.com/techpedia/143930)
- [Hermes Agent guide (Agentpedia)](https://agentpedia.codes/blog/hermes-agent-guide)
- [Runtime Hermes (ClawFleet wiki)](https://github.com/clawfleet/ClawFleet/wiki/Runtime-Hermes)
- [Hermes Agent's 5-Pillar Architecture (MindStudio)](https://www.mindstudio.ai/blog/hermes-agent-5-pillar-architecture-memory-skills-soul-crons)
- [The 8 Loops Inside Hermes Agent](https://yanxbt.substack.com/p/the-8-loops-inside-hermes-agent)
- [Hermes Agent: A PM's Field Guide](https://marily.substack.com/p/hermes-agent-a-pms-field-guide-and)
- [Hermes vs. OpenClaw: When to Reach for Which Agent (Kilo)](https://blog.kilo.ai/p/hermes-vs-openclaw-when-to-reach)
- [Hermes Agent commands cheat sheet](https://toolsbase.dev/en/reference/hermes-agent-commands)
- [Hermes Agent CLI cheat sheet (dev.to)](https://dev.to/rosgluk/hermes-agent-cli-cheat-sheet-commands-flags-and-slash-shortcuts-3pcb)
- [Hermes Agent API server docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [Hermes Agent API Server guide (Fast.io)](https://fast.io/resources/hermes-agent-api-server-guide/)
- [Using Hermes as a Python Library](https://hermes-agent.nousresearch.com/docs/guides/python-library)

**Alternatives: roundups**
- [Best OpenClaw Alternatives in 2026 (Pinggy)](https://pinggy.io/blog/best_openclaw_alternatives/)
- [Best OpenClaw Alternatives for AI Agent Workflows (Fast.io)](https://fast.io/resources/openclaw-alternatives/)
- [OpenClaw alternatives (Composio)](https://composio.dev/content/openclaw-alternatives)
- [OpenClaw alternatives (Kanerika)](https://kanerika.com/blogs/openclaw-alternatives/)
- [claude-hermes (GitHub)](https://github.com/sypsyp97/claude-hermes)

**Claude Code: Channels, headless, SDK**
- [Anthropic turns Claude Code into an always-on AI agent with new channels feature (The Decoder)](https://the-decoder.com/anthropic-turns-claude-code-into-an-always-on-ai-agent-with-new-channels-feature/)
- [What Is Claude Code Channels (Outlook Business)](https://www.outlookbusiness.com/ampstories/deeptech/what-is-claude-code-channels-how-does-it-change-ai-development-workflows-read-here)
- [Setup Claude Code + Telegram In 5 Minutes (Medium)](https://medium.com/@codeandbird/setup-claude-code-telegram-in-5-minutes-9ec49be6beb8)
- [Claude Code headless mode docs](https://docs.claude.com/en/docs/claude-code/headless)
- [Claude Code SDK headless docs](https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-headless)
- [Migrating from the OpenAI Agents SDK (Claude cookbook)](https://platform.claude.com/cookbook/claude-agent-sdk-04-migrating-from-openai-agents-sdk)
- [Claude Agent SDK for Python (GitHub)](https://github.com/anthropics/claude-agent-sdk-python)
- [Modifying system prompts (Claude docs)](https://platform.claude.com/docs/en/agent-sdk/modifying-system-prompts)
- [Migrate to Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/migration-guide)

**Anthropic auth and billing**
- [Use the Claude Agent SDK with your Claude plan (Help Center)](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Anthropic's Confusing Claude Subscription Policy, Explained (Engineer's Codex)](https://engineerscodex.com/anthropic-claude-subscription-switcharoo)
- [Anthropic officially bans third-party subscription authentication (Gigazine)](https://gigazine.net/gsc_news/en/20260220-anthropic-third-party-block)
- [Hermes issue: Agent SDK subscription credit](https://github.com/NousResearch/hermes-agent/issues/25267)
- [Model credentials and licensing (Vexa docs)](https://docs.vexa.ai/model-credentials-licensing.md)

**Codex**
- [Codex SDK (OpenAI docs)](https://developers.openai.com/codex/sdk.md)
- [Codex CLI Headless and Batch Mode guide](https://codex.danielvaughan.com/2026/04/18/codex-cli-headless-batch-mode-automation/)
- [The openai-codex Python SDK](https://codex.danielvaughan.com/2026/05/12/openai-codex-python-sdk-embedding-programmable-agent-runtime/)
- [Codex SDK (TypeScript) skill notes](https://skillsmp.com/creators/gulivan/codex-devtools/agents-skills-codex-sdk)
- [openai/codex issue: MCP tool calls in codex exec](https://github.com/openai/codex/issues/24135)
- [Headless AI agents skill (Claude Code, Codex, Gemini CLI)](https://smithery.ai/skills/x-school-academy/dev-swarm-headless-ai-agents)