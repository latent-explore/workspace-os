# Telegram vs Discord for a 24/7 Agent

## What I need
A chat app to talk to my always-on desktop agent from any device. It should be:
- Simple to use
- Easy to connect to the agent (API or MCP)
- Free
- Able to run multiple sessions (multiple chats)
- Available as desktop, web and mobile apps

## Comparison

| Criteria | Telegram | Discord |
|---|---|---|
| **Cost** | Free, including bots and full history | Free, including bots and full history |
| **Setup** | Very easy: message @BotFather, get a token | More steps: create a server, create an app, enable Message Content intent, invite the bot |
| **Connection** | Long polling or webhooks; no open ports needed | Outbound websocket; no open ports needed |
| **API / MCP** | Simple HTTP Bot API, many libraries and MCP servers | Mature API and libraries, MCP servers available |
| **Multiple sessions** | Several bots, groups, or forum topics (one topic per session) | Channels, categories and threads in one private server (most natural fit) |
| **Automation features** | Commands, inline buttons, Mini Apps, scheduled messages | Slash commands, buttons, menus, modals, embeds, per-channel permissions |
| **Message length** | 4096 characters | 2000 characters (chunk long output or attach files) |
| **Formatting** | MarkdownV2 is finicky | Standard markdown, code blocks work well |
| **Apps** | iOS, Android, desktop, web | iOS, Android, desktop, web |
| **Account needs** | Phone number | Email |
| **Security** | Allowlist my Telegram user ID | Private server with only me, plus roles |
| **Phone feel** | Faster and lighter | Heavier, but good for many channels |

## Verdict
- **Telegram:** fastest setup, best phone experience, good for a handful of sessions.
- **Discord:** better if I run 5+ parallel sessions and want channels/threads plus richer controls.

**Starting choice: Telegram.** Move to Discord only if session organization becomes painful.

## I don't have to pick just one
The two are independent. I can start with Telegram and add Discord later, even running both together.

**How to keep that easy:** put a thin adapter between the chat apps and the agent. It should:
1. Convert incoming messages to a plain internal format (session ID, sender, text, attachments).
2. Send replies back, chunking long output per platform limit.
3. Handle approvals and buttons through a simple internal interface.

Then adding Discord later means writing one more adapter, not rewriting the agent.

## Tips
- **Lock it down:** allowlist only my own account on each platform. The bot controls my desktop.
- **Don't mirror sessions across both apps at first.** It causes duplicate replies and sync problems.
- **Supervise the processes:** each adapter is a long-running process, so use a process manager that restarts it on crash.
- **Check current docs:** file size and rate limits change over time.