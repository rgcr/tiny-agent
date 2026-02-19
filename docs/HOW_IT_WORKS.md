# How It Works

## Overview

```
User Input
    |
    v
 ContextManager                  StateManager
(message history,               (hypotheses, findings,
 context slicing,                actions, summary)
 auto-summarization)                  ^
    |                                 |
    v                                 |
Provider.generate() ---- reads/updates state
    |
    |  requests a context slice
    |  converts messages to API format
    |  sends request, extracts reply
    |
    v
Reply added in ContextManager
```

## Messages

Every message is a `Message` object (`core/messages.py`) with a role
(`system`, `user`, or `assistant`), a content string, and an optional
name. Messages are always created through `ContextManager.add_message()`.

## ContextManager

`ContextManager` (`core/context.py`) holds the conversation history
as a list of messages and a rolling summary string.

**Context slicing** — providers never read the message list directly.
They call `context_slice()` with a mode to get a filtered view:

- `MODE_FULL` — all messages
- `MODE_RECENT` — system messages + last N chat turns
- `MODE_SUMMARY_PLUS_RECENT` — system prompt + summary + last N chat turns

**Auto-summarization** — when the conversation gets too long (20 turns
or ~20k tokens), the ContextManager asks the provider to summarize
the older messages, stores the summary, and keeps only the system
prompt, the summary, and the last 5 chat turns. The `maybe_summarize()`
method also updates `StateManager.context_info` with current chat count,
token count, and summarization status.

## StateManager

`StateManager` (`core/state.py`) tracks the agent's reasoning across
turns. Providers update it after every response.

- **Hypothesis** — working guess about user intent
- **Actions** — what the agent did each turn (e.g. `"reply via anthropic"`), with UTC timestamps
- **Summary** — mirrors the context summary so snapshots include it
- **Context info** — tracks chat count, token count, and summarization status for debugging

Run with `--debug` to see the full state snapshot after each turn.

## Providers

All providers extend `AIProvider` (`core/ai_providers.py`). The base
class runs the request/response cycle: build payload, call the API,
extract the reply, parse any hypothesis tag, update state.

Each provider implements four methods: `_api_headers()`,
`_build_payload()`, `_build_summary_payload()`, and `_extract_text()`.
The main difference is how they format messages — Anthropic splits
system messages into a separate field, OpenAI keeps everything in a
flat array.

`LocalProvider` is an offline provider that returns fake responses
for testing without API keys.

## REPL Loop

The CLI (`cli.py`), it's main entry point, runs a loop that does the following:

1. Parse args, load environment variables, create the provider and managers.
2. Add the system prompt to context.
3. Loop: read input, add to context, call for a possible summarization,
   call the provider, add the reply to context, then prints the reply.

The loop also handles multiline input (`\`), paste mode (`/paste` +
`/submit`), cancellation (`Ctrl+C`), and exit (`exit` / `quit`).

## Skills

> **Note:** This feature is only available in the `skills` branch.

`SkillsManager` (`core/skills.py`) discovers and loads reusable skill
files from `~/.tinyagent/skills/<name>/SKILL.md`.

**Loading** — the `/skill` command in the REPL handles discovery and
loading:

- `/skill` — lists available skills, tagging ones already loaded
- `/skill <name>` — loads the skill into context as a system message

**Trimming** — skill content is capped at 4 KB to control token growth.
The user is notified when truncation occurs.

**Safety** — path traversal (`../`) is blocked, duplicate loads are
skipped, and file system errors are caught without crashing the REPL.

**Precedence** — skill instructions override general rules except the
read-only constraint and mandatory hypothesis line.

Pass `--disable-skills` to disable the feature entirely. Loaded skills
are tracked in `StateManager` and visible with `--debug`.
