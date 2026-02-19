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
    """A single chat message with a role and content."""

    def __init__(self, role, content):
        self.role = Role.validate(role)
        self.content = content or ""

    def to_dict(self):
        """Return a serializable dictionary representation.

        Returns:
            dict: Normalized structure with message metadata.
        """

        return {
            "role": self.role,
            "content": self.content,
        }

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
