"""A tiny MCP server (stdio) with one tool, `approve`.

Claude Code is started with --permission-prompt-tool mcp__wos__approve. When a run wants a tool it is not
allowed to use, Claude Code calls `approve` with the tool name and input. We put the request in the approval
queue, wait for my answer from Telegram, and return allow or deny.

Context about the run (workspace, task, where to send the card) arrives in the WOS_CTX environment variable.
"""
import json
import os
import sys
import threading
from pathlib import Path

from .approvals import Approvals

WAIT_SECONDS = int(os.environ.get("WOS_APPROVAL_WAIT", "300"))
_write_lock = threading.Lock()


def decide(approvals: Approvals, ctx: dict, tool_name: str, tool_input: dict, wait: float = WAIT_SECONDS,
           check=None) -> dict:
    """Returns the JSON Claude Code expects: {"behavior": "allow"|"deny", ...}.

    `check` returns a Decision: allow and deny are answered here at once; only "ask" raises a card."""
    if check:
        verdict = check(ctx, tool_name, tool_input)
        if verdict.kind == "allow":
            return {"behavior": "allow", "updatedInput": tool_input}
        if verdict.kind == "deny":
            return {"behavior": "deny", "message": verdict.reason or "Not allowed."}
    rid = approvals.request(ctx, tool_name, tool_input)
    answer = approvals.wait(rid, wait)
    if answer and answer["decision"] in ("allow_once", "allow_always"):
        return {"behavior": "allow", "updatedInput": tool_input}
    if answer:
        return {"behavior": "deny", "message": "The user denied this action. Do not retry it; explain what you need or choose another way."}
    return {"behavior": "deny", "message": f"No answer from the user within {int(wait)}s. Stop here, say what you were about to do, and wait to be resumed."}


def reply(message: dict) -> None:
    with _write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def handle_call(params: dict, approvals: Approvals, ctx: dict, check) -> dict:
    args = params.get("arguments") or {}
    verdict = decide(approvals, ctx, str(args.get("tool_name", "")), args.get("input") or {}, check=check)
    return {"content": [{"type": "text", "text": json.dumps(verdict)}]}


TOOL = {
    "name": "approve",
    "description": "Ask the user whether a tool call is allowed.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "input": {"type": "object"},
            "tool_use_id": {"type": "string"},
        },
        "required": ["tool_name", "input"],
    },
}


def serve(approvals: Approvals, ctx: dict, check=None, stdin=sys.stdin) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:        # notifications need no answer
            continue
        if method == "initialize":
            version = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
            reply({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "wos", "version": "0.1"}}})
        elif method == "tools/list":
            reply({"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            def work(msg=msg, mid=mid):  # a call can wait minutes, so each runs in its own thread
                reply({"jsonrpc": "2.0", "id": mid, "result": handle_call(msg.get("params") or {}, approvals, ctx, check)})
            threading.Thread(target=work, daemon=True).start()
        elif method == "ping":
            reply({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            reply({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}})


def main() -> None:
    from . import config as cfg_mod
    from .permissions import Permissions
    from .registry import Registry

    cfg = cfg_mod.load()                       # same state dir and registry as the gateway (env is inherited)
    ctx = json.loads(os.environ.get("WOS_CTX", "{}"))
    perms = Permissions(cfg.state_dir, Registry(cfg.registry_path))
    serve(Approvals(cfg.state_dir), ctx, perms.check)


if __name__ == "__main__":
    main()
