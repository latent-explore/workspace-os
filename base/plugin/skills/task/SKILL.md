---
name: task
description: Report progress on a task and decide how the current episode ends. Use whenever you were started on a task (WOS_TASK_ID is set).
---

# Task skill

You were started on a task. The task system only remembers; you decide what happens next.
Use the helper named in `$WOS_BIN`: `$WOS_BIN task <command>`. The task id is already set for you.

## Commands
- `task show` read the task, its comments and links.
- `task comment "text"` write progress or results. Comments are the record; keep them factual.
- `task link URL --label "PR"` attach a PR, branch, results file or issue.
- `task status review|blocked|done` say where things stand (see below).
- `task resume --in 6h [--every 15m] [--check "cmd"] [--when-job-done] --reason "why"` go to sleep and be resumed later.
  `--check` is a command that exits 0 when there is something to look at. It costs nothing, so prefer it to waking blind.
- `task job start -- CMD ARGS...` start a long job (training, a big run) that outlives you. Never run long jobs in the foreground.
- `task job status` see running and finished jobs, with exit codes and log paths.
- `task notify "text"` message the user (3 lines at most).

## How an episode ends
Always leave the task in a state before you exit.
- Work is finished: comment a short summary, then `task status review` (or `done` if nothing needs review).
- You need an answer or a decision: comment the question, then `task status blocked`, then `task notify`.
- You started a job and nothing can happen until it ends: `task resume --in <time> --when-job-done`, then exit.
- If none of these fit, pick the closest and say why in a comment.

## Rules
- Do not push, merge, open pull requests, delete or spend money. Say what you would do and ask with `task notify`.
- Commit your work in the task's worktree with clear messages.
- Keep Telegram messages short. Put long output in a file and link it.
