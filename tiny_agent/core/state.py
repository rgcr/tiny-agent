"""
    tiny_agent.core.state
    ~~~~~~~~~~~~~~~~~~~~~

    Keeps track of what the agent is thinking across turns: working
    hypothesis about the user's intent, actions it took, and the
    rolling conversation summary. The provider updates this after
    every response so the agent has continuity.

"""

import json
from datetime import datetime, timezone


class StateManager(object):
    """Tracks the agent's reasoning state: hypothesis, actions,
    and summary. Passed into every generate() call so providers
    can read and update it."""

    def __init__(self):
        self.hypothesis = ""
        self.actions = []
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

        timestamp = datetime.now(timezone.utc).isoformat()
        self.actions.append({"text": text, "timestamp": timestamp})

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
            dict: Captured hypothesis, actions, and summary.
        """

        return {
            "hypothesis": self.hypothesis,
            "actions": list(self.actions),
            "summary": self.summary,
            "context_info": dict(self.context_info),
        }

    def print_snapshot(self):
        """Print the full state snapshot for debugging."""

        formatted = json.dumps(self.snapshot(), indent=2, ensure_ascii=False)
        print(f"[state] {formatted}")
