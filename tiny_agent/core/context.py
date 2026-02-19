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
    "You are 'Tiny Agent', an engineering helper with inspection tools.\n"
    "When a request implies checking this host, use your tools and report\n"
    "what you find. For purely informational questions, answer from your\n"
    "own knowledge without using tools.\n\n"
    "Rules:\n"
    "1. Start every reply with [HYPOTHESIS: ...].\n"
    "2. Keep answers brief. Present commands on their own line with two inital spaces and a\n"
    "   short comment above. No markdown formatting in responses.\n"
    "3. Cite files and commands you reference. Quote real tool output,\n"
    "   never invent results.\n"
    "4. Tools: read_file, list_files, grep, run_command (never run\n"
    "   destructive commands). run_command allows pipes (|) and fallbacks\n"
    "   (||) but blocks ;, &&, $(), and backticks. Never wrap commands in\n"
    "   bash -c or sh -c. Minimize tool calls by combining related checks:\n"
    "   ps -eo pid,comm,args | egrep -i '(nginx|postgres|redis)'\n"
    "   which docker || which python3 || echo not found\n"
    "5. Treat ## Conversation Summary, ## Agent State, and ## Skill:\n"
    "   blocks as binding context. Skills override these rules except\n"
    "   for rule 1 and the non-destructive constraint.\n"
    "6. If the user asks for something outside these limits, explain\n"
    "   the restriction.\n"
    "7. Ask follow-up questions only when needed to proceed.\n"
    "Tone: practical and direct. Always end by asking the user to confirm success or provide more data if required"
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

    def add_message(self, role, content, name=None, tool_calls=None, tool_call_id=None):
        """Append a normalized message to the context."""

        message = Message(
            role=role,
            content=content,
            name=name,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
        self.messages.append(message)
        return message

    def get_context(self):
        """Return the full context as Message instances."""

        return list(self.messages)

    def recent_messages(self, limit=5):
        """Return the last ``limit`` chat turns with their tool messages.

        Counts only user messages and plain assistant replies (no tool_calls)
        toward the limit. Tool-call assistant messages and tool results ride
        along without consuming the quota.
        """

        non_system = [msg for msg in self.messages if msg.role != Role.SYSTEM]

        # Walk backward, collecting messages until we have `limit` chat turns
        result = []
        turn_count = 0

        for msg in reversed(non_system):
            result.append(msg)
            # Count only user and plain assistant (no tool_calls) as turns
            if msg.role == Role.USER or (msg.role == Role.ASSISTANT and not msg.tool_calls):
                turn_count += 1
                if turn_count >= limit:
                    break

        result.reverse()
        return result

    def summary_plus_recent(self, limit=5):
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
        context is never silently lost. When state_manager is provided,
        its context block is appended before summarization so tool
        history and hypotheses are preserved.
        """

        if not provider:
            return

        chat_count = sum(
            1 for msg in self.messages
            if msg.role not in (Role.SYSTEM, Role.TOOL) and not msg.tool_calls
        )
        token_count = self._token_count()
        needs_trim = chat_count > self.max_turns
        needs_summary = token_count >= self.token_limit

        if not needs_trim and not needs_summary:
            if state_manager:
                state_manager.add_context_info(chat_count, token_count, summarized=False)
            return

        messages = self.get_context()
        state_block = state_manager.context_block() if state_manager else ""

        if state_block:
            messages.append(Message(Role.SYSTEM, state_block))

        summary_text, error = provider.summarize(messages)

        if state_manager:
            state_manager.add_context_info(
                chat_count, token_count,
                summarized=bool(summary_text),
                error=error,
            )

        if not summary_text:
            return

        self.summary = summary_text

        system_messages = [
            msg for msg in self.messages
            if msg.role == Role.SYSTEM and not msg.content.startswith("## Conversation Summary")
        ]
        recent = self.recent_messages(self.keep_recent)

        self.messages = system_messages
        self.messages.append(
            Message(Role.SYSTEM, f"## Conversation Summary\n{self.summary}")
        )

        self.messages.extend(recent)

    def _token_count(self):
        """Return approximate token count for non-system messages."""
        return sum(
            approx_token_count(msg.content)
            for msg in self.messages if msg.role != Role.SYSTEM
        )

    def _exceeds_token_threshold(self):
        return self._token_count() >= self.token_limit
