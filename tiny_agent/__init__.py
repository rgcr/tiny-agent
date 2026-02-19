"""
    tiny_agent
    ~~~~~~~~~~

    Public package interface for the tiny AI coding agent.

"""

from .core.context import ContextManager  # noqa: F401
from .core.state import StateManager  # noqa: F401
from .core.ai_providers import LocalProvider  # noqa: F401
from .core.ai_providers import AnthropicProvider  # noqa: F401
from .core.ai_providers import OpenAIProvider  # noqa: F401

__all__ = [
    "ContextManager",
    "StateManager",
    "LocalProvider",
    "AnthropicProvider",
    "OpenAIProvider",
]
