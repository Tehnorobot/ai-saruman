import time
from contextlib import contextmanager
from typing import Dict, Union

from langchain_core.messages import BaseMessage


class bcolors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


@contextmanager
def timing_context(name: str):
    start_time = time.time()
    yield
    end_time = time.time()
    print(f"[Timing] {name} took {end_time - start_time:.2f} seconds")


def debug_print_message(message: BaseMessage) -> None:
    separator = "-" * 50
    content = message.content
    msg_type = message.type

    # Tool calls in AIMessage
    tool_calls = getattr(message, "tool_calls", None)
    # ToolMessage — show which tool it's a response to
    tool_call_id = getattr(message, "tool_call_id", None)

    if tool_call_id:
        print(f"{separator}(tool_result)")
        if content:
            print(f"| -> {str(content)[:200]}")
        return

    # Normalize type name for display
    display_type = msg_type
    if "ai" in msg_type.lower() or "assistant" in msg_type.lower():
        display_type = "ai"

    if not isinstance(content, list):
        print(f"{separator}({display_type})")
        if content:
            for line in content.splitlines():
                print(f"| {line}")

    if tool_calls:
        for tc in tool_calls:
            name = tc.get("name", tc.get("function", {}).get("name", "?"))
            args = tc.get("args", {})
            print(f"| [→ {name}] {args}")


def debug_print_message_dict(message: Dict) -> None:
    separator = "-" * 50
    # Use repr() to show raw string with newlines, strip quotes
    content = message["content"]
    if not isinstance(content, list):
        print(f"{separator}" + f"""({message["role"]})""")
        for line in content.splitlines():
            print(f"| {line}")


# Usage example:
def print_messages(messages: list[Union[BaseMessage, Dict]]) -> None:
    print("\n --- MANUAL DEBUG VERBOSE OUTPUT --- ")
    for msg in messages:
        if isinstance(msg, dict):
            debug_print_message_dict(msg)
        else:
            debug_print_message(msg)
    print(" --> END MANUAL DEBUG VERBOSE OUTPUT <-- \n")
