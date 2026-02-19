"""
    tiny_agent.core.context
    ~~~~~~~~~~~~~~~~~~~~~~~

    Holds the conversation history as a list of Message objects. When
    the history gets too long (too many turns or too many tokens), it
    asks the provider to summarize the older messages and keeps only
    the recent ones. Providers never read the message list directly,
    they ask for a "context slice" instead.

"""

import json

from .messages import Message, Role
from .utils import approx_token_count


SYSTEM_PROMPT = (
    "## System Instructions\n\n"
    "You are a read-only engineering and sysadmin helper named 'Tiny Agent'.\n"
    "Your role is to inspect problems, explain root causes, and show the user how\n"
    "to fix them manually. Provide concise, step-by-step guidance, runnable\n"
    "commands, and verification steps so the user can reproduce everything.\n\n"
    "Rules:\n"
    "- Begin every reply with a single line: [HYPOTHESIS: <your working theory about the user's intent>]\n"
    "- Never modify files or run commands yourself. Describe what the user should do.\n"
    "- Cite the files, commands, or config names you reference.\n"
    "- When asked for examples, give concrete commands or code snippets the user can run.\n"
    "- Keep answers focused: summarize the goal, outline the solution\n"
    "- Ask the user to confirm when the solution worked or if follow-up is needed.\n"
    "- Don't overthink the response; if the request is unclear, ask for more details.\n"
    "- If a '## Conversation Summary' section is present, it contains a summary of\n"
    "  earlier conversation that was trimmed. Use it as context for your replies.\n"
)


# max_turns soft limit on the number of chat turns to keep before summarizing
MAX_TURNS = 20

# token_limit is an approximate threshold for when to trigger summarization based on token count
TOKEN_LIMIT = 20000

# Chat turns to preserve after summarization trims the context.
# (must be less than max_turns to avoid infinite summarization loops)
MESSAGES_AFTER_SUMMARIZATION = 5

# Chat turns to include in provider payloads
CONTEXT_WINDOW = MAX_TURNS


class ContextManager(object):
    """Single source of truth for the conversation. Stores messages,
    handles slicing (full, recent-only, summary+recent), and triggers
    auto-summarization when limits are hit."""

    MODE_FULL = 1
    MODE_RECENT = 2
    MODE_SUMMARY_PLUS_RECENT = 3

    def __init__(
        self, max_turns=MAX_TURNS, token_limit=TOKEN_LIMIT, keep_recent=MESSAGES_AFTER_SUMMARIZATION
    ):
        self.max_turns = max_turns if max_turns >= 1 else MAX_TURNS
        self.keep_recent = keep_recent if keep_recent >= 1 else MESSAGES_AFTER_SUMMARIZATION
        self.token_limit = token_limit if token_limit >= 1 else TOKEN_LIMIT

        # this could cause an infinite loop doing summarization
        if self.keep_recent >= self.max_turns:
            raise ValueError(
                f"keep_recent ({keep_recent}) must be less than max_turns ({self.max_turns})"
            )

        self.token_limit += approx_token_count(SYSTEM_PROMPT) + 150  # add some buffer for the system prompt and summary header

        self.messages = []
        self.summary = ""

    def add_message(self, role, content):
        """Append a normalized message to the context."""

        message = Message(role=role, content=content)
        self.messages.append(message)
        return message

    def get_context(self):
        """Return the full context as Message instances."""

        return list(self.messages)

    def recent_messages(self, limit=MESSAGES_AFTER_SUMMARIZATION):
        """Return the last ``limit`` non-system messages."""

        recent = [msg for msg in self.messages if msg.role != Role.SYSTEM]
        return list(recent[-limit:])

    def summary_plus_recent(self, limit=MESSAGES_AFTER_SUMMARIZATION):
        """Return system prompt, summary block (if any), and recent messages."""

        system_msgs = [msg for msg in self.messages if msg.role == Role.SYSTEM
                       and not msg.content.startswith("## Conversation Summary")]

        result = list(system_msgs)

        if self.summary:
            result.append(
                Message(Role.SYSTEM, f"## Conversation Summary\n{self.summary}")
            )

        result.extend(self.recent_messages(limit))
        return result

    def context_slice(self, mode=MODE_FULL, limit=CONTEXT_WINDOW):
        """Return a message list sliced according to ``mode``.

        Modes:
            MODE_FULL                – all messages as stored.
            MODE_RECENT              – system messages + last ``limit`` chat turns.
            MODE_SUMMARY_PLUS_RECENT – system prompt + summary + last ``limit`` turns.
        """

        if mode == self.MODE_FULL:
            return list(self.messages)

        if mode == self.MODE_RECENT:
            system_msgs = [msg for msg in self.messages if msg.role == Role.SYSTEM]
            return system_msgs + self.recent_messages(limit)

        if mode == self.MODE_SUMMARY_PLUS_RECENT:
            return self.summary_plus_recent(limit)

        raise ValueError(f"Unknown context slice mode: {mode}")

    def summarized_context(self):
        """Return the rolling summary maintained by the manager."""

        return self.summary

    def print_context(self):
        """Print the current context for debugging."""

        messages = [msg.to_dict() for msg in self.messages]
        formatted = json.dumps(messages, indent=2, ensure_ascii=False)
        print(f"[context] {formatted}")

    def reset(self):
        """Clear all stored messages and summary."""

        self.messages = []
        self.summary = ""

    def maybe_summarize(self, provider, state_manager=None):
        """Summarize and trim history when it exceeds limits.

        Triggers when the message count exceeds max_turns or when the
        approximate token count exceeds the threshold.  The provider is
        asked to summarize *before* older messages are dropped so that
        context is never silently lost.
        """

        if not provider:
            return

        chat_count = sum(
            1 for msg in self.messages if msg.role != Role.SYSTEM
        )
        token_count = sum(
            approx_token_count(msg.content)
            for msg in self.messages if msg.role != Role.SYSTEM
        )
        needs_trim = chat_count > self.max_turns
        needs_summary = self._exceeds_token_threshold()

        if not needs_trim and not needs_summary:
            if state_manager:
                state_manager.add_context_info(chat_count, token_count, summarized=False)
            return

        summary_text = provider.summarize(self.get_context())

        if state_manager:
            state_manager.add_context_info(
                chat_count, token_count,
                summarized=bool(summary_text),
            )

        if not summary_text:
            return

        self.summary = summary_text

        system_messages = [
            msg for msg in self.messages
            if msg.role == Role.SYSTEM and not msg.content.startswith("## Conversation Summary")
        ]
        recent = [
            msg for msg in self.messages[-self.keep_recent:]
            if msg.role != Role.SYSTEM
        ]
        self.messages = system_messages
        self.messages.append(
            Message(Role.SYSTEM, f"## Conversation Summary\n{self.summary}")
        )

        self.messages.extend(recent)

    def _exceeds_token_threshold(self):
        total = sum(
            approx_token_count(msg.content)
            for msg in self.messages if msg.role != Role.SYSTEM
        )
        return total >= self.token_limit
