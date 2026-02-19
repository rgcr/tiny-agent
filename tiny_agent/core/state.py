"""
    tiny_agent.core.state
    ~~~~~~~~~~~~~~~~~~~~~

    Keeps track of what the agent is thinking across turns: working
    hypothesis about the user's intent, actions it took, and the
    rolling conversation summary. The provider updates this after
    every response so the agent has continuity.

"""

import json


class StateManager(object):
    """Tracks the agent's reasoning state: hypothesis, actions,
    and summary. Passed into every generate() call so providers
    can read and update it."""

    MAX_DENIALS = 3

    def __init__(self):
        self.hypothesis = ""
        self.actions = []
        self.skills = []
        self.tool_events = []
        self.denial_count = 0
        self.summary = ""
        self.context_info = {}

    def set_hypothesis(self, value):
        """Store or clear the working hypothesis."""

        self.hypothesis = value or ""

    def add_action(self, text):
        """Append an action performed during reasoning.

        Args:
            text (str): Action description, e.g., "parsed command".
        """

        if not text:
            return

        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        self.actions.append({"text": text, "timestamp": timestamp})

    def add_skill(self, name, path, truncated=False):
        """Record a loaded skill to avoid duplicate loads.

        Args:
            name (str): Skill name.
            path (str): Absolute path to the SKILL.md file.
            truncated (bool): Whether the content was trimmed.
        """

        if any(s["name"] == name for s in self.skills):
            return

        self.skills.append({
            "name": name,
            "path": path,
            "truncated": truncated,
        })

    def add_denial(self):
        """Increment the command denial counter."""

        self.denial_count += 1

    @property
    def denials_exceeded(self):
        """True when the denial cap has been reached."""

        return self.denial_count >= self.MAX_DENIALS

    def add_tool_event(self, name, args, status):
        """Record a tool invocation for debugging.

        Args:
            name (str): Tool name that was called.
            args (str): Arguments passed to the tool.
            status (str): Result status ("ok", "error", "denied").
        """

        self.tool_events.append({
            "name": name,
            "args": args,
            "status": status,
        })

    def set_summary(self, summary):
        """Persist the latest context summary.

        Args:
            summary (str): Rolling session summary text.
        """

        self.summary = summary or self.summary

    def add_context_info(self, chat_count, token_count, summarized=False, error=None):
        """Record context state for debugging."""

        self.context_info = {
            "chat_count": chat_count,
            "token_count": token_count,
            "summarized": summarized,
        }
        if error:
            self.context_info["error"] = str(error)

    def snapshot(self):
        """Return a dictionary representation of the state.

        Returns:
            dict: Captured hypotheses, actions, and summary.
        """

        return {
            "hypothesis": self.hypothesis,
            "skills": list(self.skills),
            "tool_events": list(self.tool_events),
            "actions": list(self.actions),
            "denial_count": self.denial_count,
            "summary": self.summary,
            "context_info": dict(self.context_info),
        }

    def context_block(self):
        """Return a compact summary for injection into the model context.

        Only includes non-empty sections so the model isn't flooded
        with blank state fields every turn.
        """

        parts = []

        if self.hypothesis:
            parts.append(f"Working hypothesis: {self.hypothesis}")

        if self.tool_events:
            recent = self.tool_events[-5:]
            lines = [f"  - {e['name']}({e['args']}) → {e['status']}" for e in recent]
            parts.append("Recent tool calls:\n" + "\n".join(lines))

        if self.denial_count:
            parts.append(f"Command denials: {self.denial_count}/{self.MAX_DENIALS}")

        if self.summary:
            parts.append(f"Session summary: {self.summary}")

        if not parts:
            return ""

        return "## Agent State\n" + "\n".join(parts)

    def print_snapshot(self):
        """Print the full state snapshot for debugging."""

        formatted = json.dumps(self.snapshot(), indent=2, ensure_ascii=False)
        print(f"[state] {formatted}")
