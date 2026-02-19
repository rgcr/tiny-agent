"""
    tiny_agent.core.ai_providers
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Each provider knows how to talk to a specific LLM API (Anthropic,
    OpenAI, or a local offline fallback). They all share the same
    interface: take the conversation context, turn it into the right
    HTTP payload, send the request, and pull the reply text back out.

"""

import json
import os
import re

import requests

from .context import ContextManager
from .messages import Role


_HYPOTHESIS_RE = re.compile(r"^\[HYPOTHESIS:\s*(.+?)\]\s*\n?", re.MULTILINE)


DEFAULT_TIMEOUT = 60


class AIProvider(object):
    """Base class that handles the common request/response cycle.

    Subclasses only need to define how to build headers, format the
    payload, and extract the reply text. Everything else, sending
    the request, parsing hypotheses, updating state, happens here.
    """

    # avoid infinite loops of tool calls if the provider keeps asking for more
    MAX_TOOL_LOOPS = 10

    def __init__(self, name, api_url=None, api_key=None, model=None,
                 max_tokens=4096, timeout=DEFAULT_TIMEOUT, debug=False, tools=None, tool_notifier=None):
        self.name = name
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.debug = debug
        self.tools = tools
        self.tool_notifier = tool_notifier

    def _api_headers(self):
        """Return provider-specific HTTP headers."""

        raise NotImplementedError

    def _build_payload(self, context_manager, state_block=""):
        """Transform internal messages into provider-specific payload."""

        raise NotImplementedError

    def _build_summary_payload(self, transcript):
        """Build the payload for a summarization request."""

        raise NotImplementedError

    def _extract_text(self, data):
        """Extract reply text from provider response."""

        raise NotImplementedError

    def _print_debug(self, label, content):
        """Print debug information when enabled."""

        if not self.debug:
            return

        try:
            payload = json.dumps(content, indent=2, ensure_ascii=False)
        except TypeError:
            payload = str(content)

        print(f"[{self.name} {label}] {payload}")

    def _extract_hypothesis(self, text):
        """Extract and strip the [HYPOTHESIS: ...] line from a reply.

        Returns:
            tuple: (hypothesis, cleaned_text). Hypothesis is empty string
                   if the tag was not found.
        """

        match = _HYPOTHESIS_RE.search(text)
        if not match:
            return "", text

        hypothesis = match.group(1).strip()
        cleaned = text[:match.start()] + text[match.end():]
        return hypothesis, cleaned.strip()

    def _notify_tool(self, name, args):
        """Call the tool notifier callback if set."""

        if self.tool_notifier:
            self.tool_notifier(name, args)

    def _handle_tool_calls(self, data, context_manager, state_manager):
        """Process tool calls from the API response.

        Returns:
            bool: True if tool calls were handled (caller should loop).
        """

        return False

    def generate(self, context_manager, state_manager):
        """Run one full turn with tool call loop.

        Builds the payload, calls the API, and if the response contains
        tool calls, executes them, adds results to context, and re-calls
        the API. Loops until the provider returns a final text reply or
        the loop limit is reached.
        """

        if not self.api_key:
            return f"{self.name} provider requires an API key"

        for _ in range(self.MAX_TOOL_LOOPS):
            state_block = state_manager.context_block()
            payload = self._build_payload(context_manager, state_block=state_block)

            if self.debug:
                self._print_debug("request", payload)

            try:
                response = requests.post(
                    self.api_url,
                    headers=self._api_headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()

                if self.debug:
                    self._print_debug("response", data)
            except requests.RequestException as exc:
                return f"{self.name} request failed: {exc}"

            if self._handle_tool_calls(data, context_manager, state_manager):
                if state_manager.denials_exceeded:
                    return "Command denial limit reached — stopping tool execution."
                continue

            # No tool calls — extract final text reply
            summary = context_manager.summarized_context()
            if summary:
                state_manager.set_summary(summary)

            state_manager.add_action(f"reply via {self.name}")
            completion = self._extract_text(data)

            if not completion:
                completion = f"{self.name}: no action or response"

            hypothesis, completion = self._extract_hypothesis(completion)
            if hypothesis:
                state_manager.set_hypothesis(hypothesis)

            return completion

        return f"{self.name}: tool loop limit reached"

    def summarize(self, messages):
        """Condense a list of messages into a short summary string.
        Used by ContextManager when the conversation gets too long.

        Returns:
            tuple: (summary_text, error) where error is None on success.
        """

        if not self.api_key:
            return "", "no API key"

        transcript = "\n".join(msg.to_chunk() for msg in messages)
        if not transcript:
            return "", "empty transcript"

        payload = self._build_summary_payload(transcript)

        try:
            response = requests.post(
                self.api_url,
                headers=self._api_headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            return "", str(exc)

        text = self._extract_text(data)
        if not text:
            if self.debug:
                self._print_debug("summarize-empty", data)
            return "", f"empty response from API: {data}"
        return text, None


class LocalProvider(AIProvider):
    """Offline provider that returns fake responses without calling
    any API. Good for testing the REPL and context/state flow
    without needing API keys."""

    def __init__(self, debug=False):
        super(LocalProvider, self).__init__(name="local", debug=debug)

    def summarize(self, messages):
        transcript = "\n".join(msg.to_chunk() for msg in messages)
        if not transcript:
            return "", "empty transcript"

        lines = transcript.splitlines()
        goal = next((line for line in lines if line.startswith("user")), "")
        recent = lines[-1] if lines else ""
        summary_parts = ["Local summary:"]

        if goal:
            summary_parts.append(f"- Goal context: {goal.split(':',1)[-1].strip()}")

        if recent and recent != goal:
            summary_parts.append(f"- Latest turn: {recent.split(':',1)[-1].strip()}")

        if len(summary_parts) == 1:
            summary_parts.append(f"- Transcript: {transcript[-200:]}")

        return "\n".join(summary_parts), None

    def generate(self, context_manager, state_manager):
        """Return a heuristic response for the latest user message."""

        latest_user = self._latest_user_message(context_manager.get_context())
        hypothesis = self._draft_hypothesis(latest_user)
        state_manager.set_hypothesis(hypothesis)
        summary = context_manager.summarized_context()

        if summary:
            state_manager.set_summary(summary)

        reply = self._craft_reply(latest_user, state_manager)
        state_manager.add_action(f"reply via {self.name}")

        return reply

    def _latest_user_message(self, messages):
        """Return the most recent user content."""

        for msg in reversed(messages):
            if msg.role == Role.USER:
                return msg.content.strip()

        return ""

    def _draft_hypothesis(self, prompt):
        """Produce a naive hypothesis from the prompt."""

        if not prompt:
            return "Awaiting user direction"

        if "bug" in prompt.lower():
            return "User is debugging code"

        if "doc" in prompt.lower():
            return "User needs documentation"

        return "User is iterating on a coding task"

    def _craft_reply(self, prompt, state_manager):
        """Draft the assistant reply using prompt and state."""

        base = prompt or "Thanks for checking in."
        summary = state_manager.summary or "No summary yet"
        response = []
        response.append(f"I captured your request: {base}")
        response.append(f"Hypothesis: {state_manager.hypothesis}")
        response.append(f"Context summary: {summary}")
        response.append("Let me know the next step or share more detail.")
        return "\n".join(response)


class AnthropicProvider(AIProvider):
    """Talks to the Anthropic Messages API. Splits system messages
    into the separate 'system' field that Anthropic expects, and puts
    the rest in the 'messages' array."""

    def __init__(
        self, api_key=None, model="claude-sonnet-4-20250514", max_tokens=4096,
        debug=False, tools=None, tool_notifier=None
    ):
        super(AnthropicProvider, self).__init__(
            "anthropic", "https://api.anthropic.com/v1/messages",
            api_key,
            model, max_tokens,
            debug=debug,
            tools=tools,
            tool_notifier=tool_notifier,
        )
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.max_tokens = max_tokens

    def _api_headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _build_summary_payload(self, transcript):
        return {
            "model": self.model,
            "max_tokens": min(2048, self.max_tokens),
            "system": "Summarize the following engineering conversation in a few bullet points (goals, progress, blockers).",
            "messages": [
                {
                    "role": Role.USER,
                    "content": [{"type": "text", "text": transcript}],
                }
            ],
        }

    def _handle_tool_calls(self, data, context_manager, state_manager):
        """Handle Anthropic tool_use blocks."""

        if not self.tools:
            return False

        stop_reason = data.get("stop_reason")
        if stop_reason != "tool_use":
            return False

        content_blocks = data.get("content") or []
        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]

        if not tool_use_blocks:
            return False

        # extract assistant text from non-tool blocks
        text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        assistant_text = "\n".join(t for t in text_parts if t)

        # normalize tool_use blocks into our format
        tool_calls = []
        for block in tool_use_blocks:
            tool_calls.append({
                "id": block.get("id"),
                "type": "tool_use",
                "name": block.get("name"),
                "input": block.get("input") or {},
            })

        context_manager.add_message(
            Role.ASSISTANT, assistant_text, tool_calls=tool_calls,
        )

        for block in tool_use_blocks:
            name = block.get("name")
            args = block.get("input") or {}
            tool_id = block.get("id")

            result = self.tools.execute(name, args, state_manager, self.tool_notifier)

            context_manager.add_message(
                Role.TOOL, result["output"], tool_call_id=tool_id,
            )

        return True

    def _build_payload(self, context_manager, state_block=""):
        """Transform normalized messages into Anthropic wire format.

        Translates normalized tool_calls (assistant) and Role.TOOL
        messages back into Anthropic's content-block arrays.
        """

        messages = context_manager.context_slice(ContextManager.MODE_SUMMARY_PLUS_RECENT)

        system_parts = []
        chat_messages = []

        for message in messages:
            if message.role == Role.SYSTEM:
                system_parts.append(message.content)
                continue

            # assistant with tool_calls → content block array
            if message.role == Role.ASSISTANT and message.tool_calls:
                content = []
                if message.content:
                    content.append({"type": "text", "text": message.content})
                for tc in message.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc.get("input") or {},
                    })
                chat_messages.append({"role": "assistant", "content": content})
                continue

            # tool result → user message with tool_result block
            if message.role == Role.TOOL and message.tool_call_id:
                chat_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.content,
                    }],
                })
                continue

            chat_messages.append({
                "role": str(message.role),
                "content": message.content,
            })

        if state_block:
            system_parts.append(state_block)

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": chat_messages,
        }

        if system_parts:
            payload["system"] = "\n".join(system_parts)

        if self.tools:
            payload["tools"] = self.tools.definitions("anthropic")

        return payload

    def _extract_text(self, data):
        """Extract reply text from Anthropic response."""

        content = data.get("content") or []

        for block in content:
            if block.get("type") == "text":
                return (block.get("text") or "").strip()

        return ""


class OpenAIProvider(AIProvider):
    """Talks to the OpenAI Chat Completions API. Everything goes
    into a flat 'messages' array, system messages included."""

    def __init__(
        self, api_key=None, model="gpt-4o-mini", max_tokens=4096,
        debug=False, tools=None, tool_notifier=None
    ):
        super(OpenAIProvider, self).__init__(
            "openai", "https://api.openai.com/v1/chat/completions",
            api_key,
            model, max_tokens,
            debug=debug,
            tools=tools,
            tool_notifier=tool_notifier,
        )
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self.max_tokens = max_tokens

    def _api_headers(self):
        return {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    def _build_summary_payload(self, transcript):
        return {
            "model": self.model,
            "max_tokens": min(2048, self.max_tokens),
            "messages": [
                {
                    "role": Role.SYSTEM,
                    "content": "Summarize this coding session (goal, current status, next steps) in <= 5 bullet points.",
                },
                {"role": Role.USER, "content": transcript},
            ],
        }

    def _handle_tool_calls(self, data, context_manager, state_manager):
        """Handle OpenAI-style tool_calls in the response."""

        if not self.tools:
            return False

        choices = data.get("choices") or []
        if not choices:
            return False

        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            return False

        # Add the assistant message with tool_calls to context
        context_manager.add_message(
            Role.ASSISTANT,
            message.get("content") or "",
            tool_calls=tool_calls,
        )

        for call in tool_calls:
            name = call.get("function", {}).get("name")
            args = call.get("function", {}).get("arguments")
            tool_id = call.get("id")

            result = self.tools.execute(name, args, state_manager, self.tool_notifier)

            context_manager.add_message(
                Role.TOOL,
                result["output"],
                tool_call_id=tool_id,
            )

        return True

    def _build_payload(self, context_manager, state_block=""):
        """Transform internal messages into OpenAI payload."""

        messages = context_manager.context_slice(ContextManager.MODE_SUMMARY_PLUS_RECENT)

        chat_messages = []
        for message in messages:
            entry = {
                "role": message.role,
                "content": message.content,
            }
            if message.name:
                entry["name"] = message.name
            if message.tool_calls:
                entry["tool_calls"] = message.tool_calls
            if message.tool_call_id:
                entry["tool_call_id"] = message.tool_call_id
            chat_messages.append(entry)

        if state_block:
            # Insert state_block after system messages, before user content
            insert_idx = 0
            for i, entry in enumerate(chat_messages):
                if entry["role"] == Role.SYSTEM:
                    insert_idx = i + 1
                else:
                    break
            chat_messages.insert(insert_idx, {"role": Role.SYSTEM, "content": state_block})

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": chat_messages,
        }

        if self.tools:
            payload["tools"] = self.tools.definitions("openai")

        return payload

    def _extract_text(self, data):
        """Extract reply text from OpenAI response."""

        choices = data.get("choices") or []
        if not choices:
            return ""

        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()
