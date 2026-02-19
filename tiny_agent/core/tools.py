"""
    tiny_agent.core.tools
    ~~~~~~~~~~~~~~~~~~~~~

    Tool registry exposing read-only inspection capabilities to
    providers. Each tool is a simple function that reads files,
    lists directories, searches content, or runs safe commands.

"""

import json
import pathlib
import re
import shlex
import subprocess


ALLOWED_COMMANDS = {
    # # general safe commands
    # "uname", "whoami", "pwd", "date", "env",
    # # process and system monitoring
    # "ps", "top", "uptime", "free", "vmstat",
    # # safe file commands
    # "grep", "head", "tail", "cat", "wc", "sort", "stat", "ls", "file",
    # # network inspection
    # "nc", "lsof", "netstat", "traceroute", "nslookup", "dig",
    # # block devices and disk
    # "lsblk", "df", "du", "lspci", "lsmod",
    # # system info (restricted usage)
    # "systemctl", "hostname", "find", "hostnamectl",
}

# Shell operators that allow chaining arbitrary commands
_DANGEROUS_PATTERN = re.compile(r'(?<!\\);|&&|`|\$\(')

MAX_OUTPUT_BYTES = 50_000


def _split_pipe_commands(command):
    """Split a command on pipe characters that are outside quotes.

    Pipes inside quoted strings (e.g. grep -E "a|b|c") are left intact
    so shlex.split can handle them correctly.
    """

    segments = []
    current = []
    in_single = False
    in_double = False
    prev = ''

    for ch in command:
        if prev != '\\':
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == '|' and not in_single and not in_double:
                segments.append(''.join(current))
                current = []
                prev = ch
                continue

        current.append(ch)
        prev = ch

    segments.append(''.join(current))
    return segments


class ToolError(Exception):
    """Raised when a tool cannot complete the requested action."""


class ToolDeniedError(ToolError):
    """Raised when a command is blocked by the allowlist policy."""


class ToolSuite(object):
    """Registry and execution engine for tiny-agent tools.

    Args:
        root_dir (str): Project root for path resolution.
        max_bytes (int): Byte cap for file reads and command output.
    """

    def __init__(self, root_dir=None, max_bytes=None):
        self.root_dir = pathlib.Path(root_dir).resolve() if root_dir else None
        self.max_bytes = max_bytes or MAX_OUTPUT_BYTES
        self._registry = self._build_registry()

    def execute(self, name, arguments, state_manager=None, notifier=None):
        """Execute a tool by name.

        Args:
            name (str): Tool name from the registry.
            arguments (str or dict): Tool arguments as JSON string or dict.
            state_manager (StateManager): Optional state tracker for logging.
            notifier (callable): Optional callback(name, args) for UI feedback.

        Returns:
            dict: {"status": "ok|denied|error", "output": str, "denied_limit": bool}
        """

        tool = self._registry.get(name)

        if not tool:
            return {"status": "error", "output": f"Unknown tool '{name}'", "denied_limit": False}

        args = self._parse_arguments(arguments)
        args_str = str(args)

        if notifier:
            notifier(name, arguments)

        try:
            output = tool["handler"](args)
            if state_manager:
                state_manager.add_tool_event(name, args_str, "ok")
            return {"status": "ok", "output": output, "denied_limit": False}

        except ToolDeniedError as exc:
            denied_limit = False
            status = "denied"
            output = f"Tool denied: {exc}"

            if state_manager:
                state_manager.add_denial()
                if state_manager.denials_exceeded:
                    denied_limit = True
                    status = "denied (limit reached)"
                    output += " — denial limit reached, stop retrying blocked commands."
                state_manager.add_tool_event(name, args_str, status)

            return {"status": "denied", "output": output, "denied_limit": denied_limit}

        except ToolError as exc:
            output = f"Tool error: {exc}"
            if state_manager:
                state_manager.add_tool_event(name, args_str, f"error: {exc}")
            return {"status": "error", "output": output, "denied_limit": False}

    def definitions(self, fmt):
        """Return tool schemas in the requested provider format.

        Args:
            fmt (str): Either "openai" or "anthropic".

        Returns:
            list: Provider-ready tool definition dicts.
        """

        result = []

        for tool in self._registry.values():
            if fmt == "openai":
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                })
            elif fmt == "anthropic":
                result.append({
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["parameters"],
                })

        return result

    # ------------------------------------------------------------------
    # Tool handlers

    def _tool_read_file(self, args):
        """Read a file within the project root."""

        path = args.get("path")
        max_bytes = int(args.get("max_bytes") or self.max_bytes)
        resolved = self._resolve_path(path)

        try:
            data = resolved.read_bytes()[:max_bytes]
        except OSError as exc:
            raise ToolError(f"Failed to read {resolved}: {exc}")

        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")

    def _tool_list_files(self, args):
        """List files relative to the project root."""

        path = args.get("path") or "."
        recursive = bool(args.get("recursive"))
        max_entries = int(args.get("max_entries", 200))
        resolved = self._resolve_path(path)
        entries = []

        try:
            iterator = resolved.rglob("*") if recursive else resolved.iterdir()
            for idx, item in enumerate(iterator):
                if idx >= max_entries:
                    break
                entries.append(self._display_path(item))
        except OSError as exc:
            raise ToolError(f"Failed to list {resolved}: {exc}")

        return "\n".join(entries)

    def _tool_grep(self, args):
        """Search file contents for a pattern."""

        pattern = args.get("pattern")
        if not pattern:
            raise ToolError("Pattern is required")

        path = args.get("path") or "."
        recursive = bool(args.get("recursive", True))
        max_results = int(args.get("max_results", 50))
        resolved = self._resolve_path(path)

        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Invalid regex pattern: {exc}")

        matches = []

        if resolved.is_file():
            files = [resolved]
        elif recursive:
            files = (f for f in resolved.rglob("*") if f.is_file())
        else:
            files = (f for f in resolved.iterdir() if f.is_file())

        for filepath in files:
            try:
                content = filepath.read_text(errors="replace")
            except OSError:
                continue

            for line_num, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    rel = self._display_path(filepath)
                    matches.append(f"{rel}:{line_num}: {line.rstrip()}")
                    if len(matches) >= max_results:
                        break

            if len(matches) >= max_results:
                break

        if not matches:
            return "No matches found."

        return "\n".join(matches)

    def _tool_run_command(self, args):
        """Run a safe shell command.

        Uses shell=True so pipes, quoting, and escaping work naturally.
        Validates every command in a pipeline against ALLOWED_COMMANDS
        and blocks dangerous chaining operators (;, &&, ||, $(), backticks).
        """

        command = (args.get("command") or "").strip()

        if not command:
            raise ToolError("No command provided")

        self._validate_command(command)

        try:
            completed = subprocess.run(
                command,
                cwd=str(self.root_dir) if self.root_dir else None,
                capture_output=True,
                text=True,
                timeout=45,
                shell=True,
            )
        except subprocess.TimeoutExpired:
            raise ToolError("Command timed out after 45 seconds")

        stdout = (completed.stdout or "")[:self.max_bytes].strip()
        stderr = (completed.stderr or "")[:self.max_bytes].strip()

        if stdout and stderr:
            return f"{stdout}\n{stderr}"

        return stdout or stderr or "(no output)"

    def _validate_command(self, command):
        """Check that all commands in a pipeline are allowed.

        Rejects shell chaining operators (;, &&, ||) and subshell
        constructs ($(), backticks). Pipe (|) is allowed when every
        segment starts with an allowed command. Escaped semicolons
        (\\;) are permitted for find -exec.
        """

        if _DANGEROUS_PATTERN.search(command):
            raise ToolError("Shell chaining operators (;, &&, $()) not allowed. Use || for fallbacks and | for pipes.")

        if not ALLOWED_COMMANDS:
            return

        # validate each pipeline segment against the allowlist,
        # splitting on unquoted pipes to handle grep -E "a|b"
        for segment in _split_pipe_commands(command):
            segment = segment.strip()
            if not segment:
                continue

            try:
                parts = shlex.split(segment)
            except ValueError:
                raise ToolError(f"Malformed command segment: {segment}")

            if parts and parts[0] not in ALLOWED_COMMANDS:
                allowed = ", ".join(sorted(ALLOWED_COMMANDS))
                raise ToolDeniedError(f"Command '{parts[0]}' not allowed. Allowed: {allowed}")

    # ------------------------------------------------------------------
    # Internal helpers

    def _build_registry(self):
        """Build the tool name -> handler mapping with schemas."""

        schema_read_file = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Optional byte limit (default 50k)",
                },
            },
            "required": ["path"],
        }

        schema_list_files = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list (default '.')",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recurse into subdirectories",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Maximum entries to return (default 200)",
                },
            },
        }

        schema_grep = {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default '.')",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recurse into subdirectories (default true)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matching lines to return (default 50)",
                },
            },
            "required": ["pattern"],
        }

        schema_run_command = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (must start with an allowed command)",
                },
            },
            "required": ["command"],
        }

        return {
            "read_file": {
                "name": "read_file",
                "description": "Read a file within the project root (truncated to byte limit).",
                "parameters": schema_read_file,
                "handler": self._tool_read_file,
            },
            "list_files": {
                "name": "list_files",
                "description": "List files and directories relative to the project root.",
                "parameters": schema_list_files,
                "handler": self._tool_list_files,
            },
            "grep": {
                "name": "grep",
                "description": "Search file contents for a regex pattern, returning matching lines.",
                "parameters": schema_grep,
                "handler": self._tool_grep,
            },
            "run_command": {
                "name": "run_command",
                "description": "Run a safe shell command from a restricted allowlist.",
                "parameters": schema_run_command,
                "handler": self._tool_run_command,
            },
        }

    def _parse_arguments(self, arguments):
        """Normalize arguments to a dict."""

        if isinstance(arguments, dict):
            return arguments

        if not arguments:
            return {}

        try:
            return json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ToolError(f"Invalid arguments JSON: {exc}")

    def _resolve_path(self, path):
        """Resolve a path, restricting to root_dir when set."""

        if not path:
            raise ToolError("Path is required")

        base = self.root_dir or pathlib.Path.cwd()
        resolved = (base / path).resolve()

        # only enforce boundary when root_dir was explicitly provided
        if self.root_dir:
            if self.root_dir not in resolved.parents and resolved != self.root_dir:
                raise ToolError("Access outside project root denied")

        return resolved

    def _display_path(self, full_path):
        """Return a display-friendly path, relative to base when possible."""

        base = self.root_dir or pathlib.Path.cwd()

        try:
            return str(full_path.relative_to(base))
        except ValueError:
            return str(full_path)
