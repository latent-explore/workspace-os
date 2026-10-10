# Workspace OS: Vision and Features

## Vision
Help me scale myself.

Onboard any new workspace and have Workspace OS absorb it. Each one then runs with minimal time, effort and attention from me. Over time my reach grows across business, profession and personal life, without my workload growing with it.

## Initial workspaces

| Workspace | What it does |
|---|---|
| **Email** | Filters spam, deletes useless mail, sends a daily summary. |
| **Calendar** | Builds and manages my calendar from emails and reminders, and plans my days. |
| **AI Updates** | Tracks AI news and papers; sends daily and weekly summaries of the top ones. |
| **Research Assistant** | Keeps a knowledge base of papers I read, helps write technical blogs, runs experiments, and tracks open source contributions. Each research project can be its own workspace. |

Workspaces will talk to each other. Example: an interesting paper from **AI Updates** goes into the **Research Assistant** knowledge base; a deadline in an email goes into **Calendar**.

## Core
- **Always on:** runs 24/7 on the desktop, no sleep.
- **Fault tolerance:** services restart after a crash or reboot; unfinished tasks resume.
- **Task queue:** GitHub issues are tasks; agents always have the next one.
- **Parallel tasks:** one git worktree per task, so tasks never clash.
- **Multi-repo tasks:** one feature can touch several repos, one PR each.
- **Scheduler (cron):** timed jobs for summaries, planning and experiment check-ins, with no token cost until a job fires.
- **Rate-limit handling:** pauses at the token limit, resumes after the reset.
- **Concurrency cap:** limits how many agents run at once.

## Talking to it
- **Chat control:** talk to the system from my phone or desktop.
- **Telegram first, Discord later:** one thin adapter per app, same workflow behind both.
- **One topic/thread per task:** each conversation is its own session.
- **Short replies:** at most 5 lines, one question at a time.
- **Approval buttons:** Approve / Change / Discuss on every plan.
- **Alerts:** only urgent things reach me directly.
- **Allowlist:** only my own account can control it.

## Workspaces
- **Easy onboarding:** `/workspace-os add` absorbs a new or existing workspace in minutes.
- **Register in place:** existing workspaces are added without being moved.
- **Template for new ones:** each starts with shared skills and past learnings.
- **Low maintenance:** a workspace runs on its own; I only see summaries and decisions.
- **Cross-workspace links:** one workspace can hand information or tasks to another.
- **Setup skill:** `/workspace-os setup`, safe to re-run.
- **Rebuild from GitHub:** a new machine can be restored from the registry alone.
- **Two instances:** personal and company copies from the same code, fully separate.

## Outside-world access
- **Connectors:** email, calendar, web and paper sources, each behind one small skill.
- **Least access:** every connector gets only the permissions it needs.
- **Safe by default:** agents can read and draft freely. Sending, deleting and replying to others need my approval at first. Trusted actions (e.g. deleting obvious spam) can be allowed later, one rule at a time.

## Quality and control
- **Every change is a PR:** agent output, and agent self-improvements, wait for my approval.
- **Auto code review:** an agent reviews each PR before I see it.
- **Approval gates:** merges, deploys, spending, deletions and messages to other people need me.
- **Safe sandbox:** agents run as a separate Linux user with CPU/RAM limits.
- **Secrets kept out of repos:** one locked file, outside every repo.
- **Git everything:** anything can be recovered.

## Learning
- **Run logs:** every run is saved with a summary row (tokens, status, result).
- **Improver:** studies failures and proposes better skills and prompts as PRs.
- **Shared learnings:** good fixes flow up to the base, so every workspace gets them.
- **Housekeeper:** nightly cleanup of dead code, stale docs, old worktrees.
- **Global index:** answers "what did I do this year?" across all workspaces.

## Reports
- **Daily brief and weekly review:** built from real data (commits, PRs, tokens, emails handled), same template each time.
- **Token budget tracking:** usage split by job type.
- **Dashboard (later):** Grafana charts for runs, tokens and success rate.

## Later
- **Showcase site:** public page for my best work.
- **Share agents:** let other people use them under my control.
- **More workspaces:** business, finance, health and anything else I add.
