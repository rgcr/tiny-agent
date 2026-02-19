"""
    tiny_agent.cli
    ~~~~~~~~~~~~~~

    The main REPL loop. Reads user input, feeds it through the
    ContextManager and provider, and prints the reply. Handles
    multiline input, paste mode, and calls for a possible
    summarization between turns.

"""

import argparse
import sys

from tiny_agent.core.context import ContextManager, SYSTEM_PROMPT
from tiny_agent.core.state import StateManager
from tiny_agent.core.ai_providers import (
    LocalProvider,
    AnthropicProvider,
    OpenAIProvider
)
from tiny_agent.core.messages import Role
from tiny_agent.core.utils import colorize, load_env_files, parse_debug_flags, debug_enabled


def main(argv=None):
    """Run the REPL loop."""

    load_env_files()
    args = parse_args(argv)

    debug_flags = parse_debug_flags(args.debug)

    provider = build_provider(
        args.provider,
        args.model,
        debug_enabled("requests", debug_flags),
        args.api_key,
    )

    state_manager = StateManager()
    context_manager = ContextManager()
    context_manager.add_message(Role.SYSTEM, SYSTEM_PROMPT)

    color_enabled = not args.no_color

    banner = f"""
                        Tiny Agent
 ================================================================

  [Provider: {provider.name} | Model: {getattr(provider, "model", "n/a")}]
           __
          / ')
   .-^^^-/ /      - Use '\\' for multiline input
__/       /       - /paste - paste mode (/submit to send request)
<__.|_|-|_|       - Type 'exit' or 'quit' to leave
 ================================================================
"""
    print(colorize(banner, "yellow", color_enabled))

    buffer = []
    while True:
        try:
            # handle user prompt
            prompt_symbol = "... " if buffer else ">>> "
            prefix = "\n" if buffer else ""
            prompt = prefix + colorize(prompt_symbol, "cyan", color_enabled)
            user_input = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        # handle 'paste mode'
        if user_input == "/paste":
            print(colorize("Paste mode enabled. Finish with /submit", "gray", color_enabled))
            pasted = []

            while True:
                try:
                    line = input().rstrip("\n")
                except (KeyboardInterrupt, EOFError):
                    line = "/submit"

                if line == "/submit":
                    break

                pasted.append(line)

            user_input = "\n".join(pasted)

        # handle multiline input
        if user_input.endswith("\\"):
            buffer.append(user_input[:-1])
            continue

        if buffer:
            buffer.append(user_input)
            user_input = "\n".join(part.rstrip() for part in buffer)
            buffer = []

        if not user_input:
            continue

        # if user want to leave
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            break

        # add input to context
        context_manager.add_message(Role.USER, user_input)

        # summarize if we reach token threshold or message count threshold
        context_manager.maybe_summarize(provider, state_manager)

        print(colorize("\nThinking…", "gray", color_enabled))

        try:
            # AI api call
            reply = provider.generate(context_manager, state_manager)
        except KeyboardInterrupt:
            print(colorize("(request cancelled)", "gray", color_enabled))
            # let the provider know we cancelled a request in next call
            context_manager.add_message(Role.ASSISTANT, "(cancelled)")
            # skip and wait for new input
            continue

        # add the AI response to our context
        context_manager.add_message(Role.ASSISTANT, reply)
        # print response
        agent_label = colorize("\ntiny-agent:\n\n", "yellow", color_enabled)
        colored_reply = colorize(reply, "yellow", color_enabled)
        print(f"{agent_label} {colored_reply}\n")

        if debug_enabled("state", debug_flags):
            state_manager.print_snapshot()

        if debug_enabled("context", debug_flags):
            context_manager.print_context()


def parse_args(argv):
    """Parse CLI arguments for provider selection."""

    parser = argparse.ArgumentParser(
        description="Interactive tiny-agent CLI for AI guidance"
    )
    parser.add_argument(
        "--provider",
        default="local",
        choices=["local", "anthropic", "openai"],
        help="LLM backend to invoke (local heuristic, Anthropic, OpenAI)",
    )
    parser.add_argument(
        "--model",
        help="Optional provider-specific model identifier",
    )
    parser.add_argument(
        "--api-key",
        help="API key override for Anthropic/OpenAI providers",
    )
    parser.add_argument(
        "--debug",
        nargs="?",
        const="all",
        help="Debug categories: state, context, requests (comma-separated, or 'all')",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI coloring in output",
    )
    return parser.parse_args(argv if argv is not None else sys.argv[1:])


def build_provider(provider_name, model, debug, api_key):
    """Instantiate the provider requested on the CLI."""

    normalized = (provider_name or "local").lower()

    if normalized == "local":
        return LocalProvider(debug=debug)

    if normalized == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=model or "claude-3-haiku-20240307",
            debug=debug,
        )

    if normalized == "openai":
        return OpenAIProvider(
            api_key=api_key,
            model=model or "gpt-4o-mini",
            debug=debug,
        )

    raise ValueError(f"Unknown provider '{provider_name}'")


if __name__ == "__main__":
    main()
