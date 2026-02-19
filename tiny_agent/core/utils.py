"""
    tiny_agent.core.utils
    ~~~~~~~~~~~~~~~~~~~~~

    Shared utility functions used across the tiny-agent package.

"""

def colorize(text, color, enabled):
    """Apply ANSI color codes when enabled."""

    if not enabled:
        return text

    codes = {
        "yellow": "33",
        "cyan": "36",
        "gray": "90",
    }
    code = codes.get(color)

    if not code:
        return text

    return f"\033[{code}m{text}\033[0m"


def load_env_files():
    """Load environment variables from .env files if present."""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    for path in (".env", ".env.local"):
        load_dotenv(dotenv_path=path, override=False)


def approx_token_count(text):
    """Return a rough token count using a 4-chars-per-token heuristic."""

    if not text:
        return 0

    return max(1, int(len(text) / 4))


def parse_debug_flags(raw):
    """Parse the --debug flag into a set of categories."""

    if not raw:
        return set()

    flags = set(raw.split(","))

    if "all" in flags:
        return {"context", "state", "requests"}

    return flags


def debug_enabled(category, flags):
    """Check if a debug category is active."""

    return category in flags
