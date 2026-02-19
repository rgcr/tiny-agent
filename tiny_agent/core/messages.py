"""
    tiny_agent.core.messages
    ~~~~~~~~~~~~~~~~~~~~~~~~

    The common message format that all providers share. Every message
    has a role (system, user, or assistant) and content. Providers
    convert these into whatever their API expects.

"""

from enum import Enum


class Role(str, Enum):
    """The three roles a message can have: system, user, or assistant."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

    def __str__(self):
        return self.value

    def __format__(self, format_spec):
        return str.__format__(self.value, format_spec)

    @classmethod
    def validate(cls, role):
        """Ensure role is known and return the enum member."""

        if role is None:
            return cls.USER

        if isinstance(role, cls):
            return role

        value = str(role)
        if value not in cls._value2member_map_:
            raise ValueError(f"Unsupported role: {value}")

        return cls._value2member_map_[value]


class Message(object):
    """A single chat message with a role, content, and optional name."""

    def __init__(self, role, content, name=None, tool_calls=None, tool_call_id=None):
        self.role = Role.validate(role)
        self.content = content or ""
        self.name = name
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id

    def to_dict(self):
        """Return a serializable dictionary representation.

        Returns:
            dict: Normalized structure with message metadata.
        """

        result = {
            "role": self.role,
            "content": self.content,
        }

        if self.name:
            result["name"] = self.name

        if self.tool_calls:
            result["tool_calls"] = self.tool_calls

        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id

        return result

    @classmethod
    def from_dict(cls, payload):
        """Hydrate a Message from a dictionary payload.

        Args:
            payload (dict): Source dictionary.

        Returns:
            Message: New instance populated with the provided data.
        """

        return cls(
            role=payload.get("role"),
            content=payload.get("content"),
            name=payload.get("name"),
            tool_calls=payload.get("tool_calls"),
            tool_call_id=payload.get("tool_call_id"),
        )

    def __repr__(self):
        """Return the debug representation.

        Returns:
            str: Debug-friendly summary.
        """

        base = f"<Message role={self.role} content={self.content[:30]}"
        return f"{base}>"

    def to_chunk(self):
        """Return a summary-friendly string: 'role: content'.

        Returns:
            str: Formatted line for use in summarization transcripts.
        """

        return f"{self.role}: {self.content.strip()}"
